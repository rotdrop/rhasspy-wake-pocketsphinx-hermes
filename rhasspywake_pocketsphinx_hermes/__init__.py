"""Hermes MQTT server for Rhasspy wakeword with pocketsphinx"""
import asyncio
import io
import logging
import queue
import socket
import tempfile
import threading
import typing
import wave
from dataclasses import dataclass, field
from pathlib import Path

import pocketsphinx
from rhasspyhermes.audioserver import AudioFrame
from rhasspyhermes.base import Message
from rhasspyhermes.client import GeneratorType, HermesClient, TopicArgs
from rhasspyhermes.wake import (
    HotwordDetected,
    HotwordError,
    HotwordToggleOff,
    HotwordToggleOn,
    HotwordToggleReason,
)

WAV_HEADER_BYTES = 44
_LOGGER = logging.getLogger("rhasspywake_pocketsphinx_hermes")

# -----------------------------------------------------------------------------


@dataclass
class SiteInfo:
    """Self-contained information for a single site"""

    site_id: str
    enabled: bool = True
    disabled_reasons: typing.Set[str] = field(default_factory=set)
    detection_thread: typing.Optional[threading.Thread] = None
    audio_buffer: bytes = bytes()
    first_audio: bool = True
    decoder: typing.Optional[pocketsphinx.Decoder] = None
    decoder_started: bool = False

    # Queue of (bytes, is_raw)
    wav_queue: "queue.Queue[typing.Tuple[bytes, bool]]" = field(
        default_factory=queue.Queue
    )


# -----------------------------------------------------------------------------


class WakeHermesMqtt(HermesClient):
    """Hermes MQTT server for Rhasspy wakeword with pocketsphinx."""

    def __init__(
        self,
        client,
        keyphrase: str,
        acoustic_model: Path,
        dictionary_paths: typing.List[Path],
        wakeword_id: str = "",
        keyphrase_threshold: float = 1e-40,
        mllr_matrix: typing.Optional[Path] = None,
        site_ids: typing.Optional[typing.List[str]] = None,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        chunk_size: int = 960,
        udp_audio: typing.Optional[typing.List[typing.Tuple[str, int, str]]] = None,
        udp_chunk_size: int = 2048,
        udp_raw_audio: typing.Optional[typing.Iterable[str]] = None,
        udp_forward_mqtt: typing.Optional[typing.Iterable[str]] = None,
        debug: bool = False,
        lang: typing.Optional[str] = None,
    ):
        super().__init__(
            "rhasspywake_pocketsphinx_hermes",
            client,
            sample_rate=sample_rate,
            sample_width=sample_width,
            channels=channels,
            site_ids=site_ids,
        )

        self.subscribe(AudioFrame, HotwordToggleOn, HotwordToggleOff)

        self.keyphrase = keyphrase
        self.keyphrase_threshold = keyphrase_threshold

        self.acoustic_model = acoustic_model
        self.dictionary_paths = dictionary_paths
        self.mllr_matrix = mllr_matrix

        self.wakeword_id = wakeword_id

        # Required audio format
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels

        self.chunk_size = chunk_size

        self.site_info: typing.Dict[str, SiteInfo] = {}

        # Create site information for known sites
        for site_id in self.site_ids:
            site_info = SiteInfo(site_id=site_id)

            # Create and start detection thread
            site_info.detection_thread = threading.Thread(
                target=self.detection_thread_proc, daemon=True, args=(site_info,)
            )
            site_info.detection_thread.start()

            self.site_info[site_id] = site_info

        self.debug = debug

        self.lang = lang

        # Listen for raw audio on UDP too
        self.udp_chunk_size = udp_chunk_size

        # Site ids where UDP audio is raw 16Khz, 16-bit mono PCM chunks instead
        # of WAV chunks.
        self.udp_raw_audio = set(udp_raw_audio or [])

        # Site ids where UDP audio should be forward to MQTT after detection.
        self.udp_forward_mqtt = set(udp_forward_mqtt or [])

        if udp_audio:
            for udp_host, udp_port, udp_site_id in udp_audio:
                threading.Thread(
                    target=self.udp_thread_proc,
                    args=(udp_host, udp_port, udp_site_id),
                    daemon=True,
                ).start()

    # -------------------------------------------------------------------------

    def load_decoder(self, site_info: SiteInfo):
        """Load Pocketsphinx decoder."""
        _LOGGER.debug(
            "Loading decoder with hmm=%s, dicts=%s",
            str(self.acoustic_model),
            self.dictionary_paths,
        )

        words_needed = set(self.keyphrase.split())

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt") as dict_file:
            # Combine all dictionaries
            for sub_dict_path in self.dictionary_paths:
                if not sub_dict_path.is_file():
                    _LOGGER.warning("Skipping dictionary %s", str(sub_dict_path))
                    continue

                with open(sub_dict_path, "r") as sub_dict_file:
                    for line in sub_dict_file:
                        line = line.strip()
                        if line:
                            word = line.split(maxsplit=2)[0]
                            if word in words_needed:
                                print(line, file=dict_file)
                                words_needed.remove(word)

            assert (
                len(words_needed) == 0
            ), f"Missing pronunciations for words: {words_needed}"
            dict_file.seek(0)

            decoder_config = pocketsphinx.Decoder.default_config()
            decoder_config.set_string("-hmm", str(self.acoustic_model))
            decoder_config.set_string("-dict", str(dict_file.name))
            decoder_config.set_string("-keyphrase", self.keyphrase)
            decoder_config.set_float("-kws_threshold", self.keyphrase_threshold)

            if not self.debug:
                decoder_config.set_string("-logfn", "/dev/null")

            if self.mllr_matrix and self.mllr_matrix.is_file():
                decoder_config.set_string("-mllr", str(self.mllr_matrix))

            site_info.decoder = pocketsphinx.Decoder(decoder_config)

    # -------------------------------------------------------------------------
    def stop(self):
        """Stop detection threads."""
        _LOGGER.debug("Stopping detection threads...")

        for site_info in self.site_info.values():
            if site_info.detection_thread is not None:
                site_info.wav_queue.put((None, None))
                site_info.detection_thread.join()
                site_info.detection_thread = None

            site_info.porcupine = None

        _LOGGER.debug("Stopped")

    # -------------------------------------------------------------------------

    async def handle_audio_frame(self, wav_bytes: bytes, site_id: str = "default"):
        """Process a single audio frame"""
        site_info = self.site_info.get(site_id)
        if site_info is None:
            # Create information for new site
            site_info = SiteInfo(site_id=site_id)
            site_info.detection_thread = threading.Thread(
                target=self.detection_thread_proc, daemon=True, args=(site_info,)
            )

            site_info.detection_thread.start()
            self.site_info[site_id] = site_info

        site_info.wav_queue.put((wav_bytes, False))

    async def handle_detection(
        self, wakeword_id: str, site_id: str = "default"
    ) -> typing.AsyncIterable[
        typing.Union[typing.Tuple[HotwordDetected, TopicArgs], HotwordError]
    ]:
        """Handle a successful hotword detection"""
        try:
            yield (
                HotwordDetected(
                    site_id=site_id,
                    model_id=self.keyphrase,
                    current_sensitivity=self.keyphrase_threshold,
                    model_version="",
                    model_type="personal",
                    lang=self.lang,
                ),
                {"wakeword_id": wakeword_id},
            )
        except Exception as e:
            _LOGGER.exception("handle_detection")
            yield HotwordError(error=str(e), context=self.keyphrase, site_id=site_id)

    def detection_thread_proc(self, site_info: SiteInfo):
        """Handle WAV audio chunks."""
        try:
            while True:
                wav_bytes, is_raw = site_info.wav_queue.get()
                if wav_bytes is None:
                    # Shutdown signal
                    break

                if site_info.first_audio:
                    _LOGGER.debug("Receiving audio %s", site_info.site_id)
                    site_info.first_audio = False

                if not site_info.decoder:
                    self.load_decoder(site_info)

                assert site_info.decoder is not None

                if is_raw:
                    # Raw audio chunks
                    audio_data = wav_bytes
                else:
                    # WAV chunks
                    audio_data = self.maybe_convert_wav(wav_bytes)

                # Add to persistent buffer
                site_info.audio_buffer += audio_data

                # Process in chunks.
                # Any remaining audio data will be kept in buffer.
                while len(site_info.audio_buffer) >= self.chunk_size:
                    chunk = site_info.audio_buffer[: self.chunk_size]
                    site_info.audio_buffer = site_info.audio_buffer[self.chunk_size :]

                    if not site_info.decoder_started:
                        # Begin utterance
                        site_info.decoder.start_utt()
                        site_info.decoder_started = True

                    site_info.decoder.process_raw(chunk, False, False)
                    hyp = site_info.decoder.hyp()
                    if hyp:
                        if site_info.decoder_started:
                            # End utterance
                            site_info.decoder.end_utt()
                            site_info.decoder_started = False

                        wakeword_id = self.wakeword_id
                        if not wakeword_id:
                            wakeword_id = self.keyphrase

                        assert self.loop is not None
                        asyncio.run_coroutine_threadsafe(
                            self.publish_all(
                                self.handle_detection(
                                    wakeword_id, site_id=site_info.site_id
                                )
                            ),
                            self.loop,
                        )

                        # Stop and clear buffer to avoid duplicate reports
                        site_info.audio_buffer = bytes()
                        break

        except Exception:
            _LOGGER.exception("detection_thread_proc")

    # -------------------------------------------------------------------------

    def udp_thread_proc(self, host: str, port: int, site_id: str):
        """Handle WAV chunks from UDP socket."""
        try:
            site_info = self.site_info[site_id]
            is_raw_audio = site_id in self.udp_raw_audio
            forward_to_mqtt = site_id in self.udp_forward_mqtt

            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.bind((host, port))
            _LOGGER.debug(
                "Listening for audio on UDP %s:%s (siteId=%s, raw=%s)",
                host,
                port,
                site_id,
                is_raw_audio,
            )

            chunk_size = self.udp_chunk_size
            if is_raw_audio:
                chunk_size += WAV_HEADER_BYTES

            while True:
                wav_bytes, _ = udp_socket.recvfrom(chunk_size)

                if site_info.enabled:
                    site_info.wav_queue.put((wav_bytes, is_raw_audio))
                elif forward_to_mqtt:
                    # When the wake word service is disabled, ASR should be active
                    if is_raw_audio:
                        # Re-package as WAV chunk and publish to MQTT
                        with io.BytesIO() as wav_buffer:
                            wav_file: wave.Wave_write = wave.open(wav_buffer, "wb")
                            with wav_file:
                                wav_file.setframerate(self.sample_rate)
                                wav_file.setsampwidth(self.sample_width)
                                wav_file.setnchannels(self.channels)
                                wav_file.writeframes(wav_bytes)

                            publish_wav_bytes = wav_buffer.getvalue()
                    else:
                        # Use WAV chunk as-is
                        publish_wav_bytes = wav_bytes

                    self.publish(
                        AudioFrame(wav_bytes=publish_wav_bytes),
                        site_id=site_info.site_id,
                    )
        except Exception:
            _LOGGER.exception("udp_thread_proc")

    # -------------------------------------------------------------------------

    async def on_message_blocking(
        self,
        message: Message,
        site_id: typing.Optional[str] = None,
        session_id: typing.Optional[str] = None,
        topic: typing.Optional[str] = None,
    ) -> GeneratorType:
        """Received message from MQTT broker."""
        # Check enable/disable messages
        site_info = self.site_info.get(site_id) if site_id else None

        if isinstance(message, HotwordToggleOn):
            if site_info:
                if message.reason == HotwordToggleReason.UNKNOWN:
                    # Always enable on unknown
                    site_info.disabled_reasons.clear()
                else:
                    site_info.disabled_reasons.discard(message.reason)

                if site_info.disabled_reasons:
                    _LOGGER.debug("Still disabled: %s", site_info.disabled_reasons)
                else:
                    site_info.enabled = True
                    site_info.first_audio = True

                    _LOGGER.debug("Enabled")
        elif isinstance(message, HotwordToggleOff):
            if site_info:
                site_info.enabled = False
                site_info.disabled_reasons.add(message.reason)

                # End utterance
                if site_info.decoder and site_info.decoder_started:
                    site_info.decoder.end_utt()
                    site_info.decoder_started = False

                _LOGGER.debug("Disabled")
        elif isinstance(message, AudioFrame):
            if site_info and site_info.enabled:
                await self.handle_audio_frame(
                    message.wav_bytes, site_id=site_info.site_id
                )
        else:
            _LOGGER.warning("Unexpected message: %s", message)

        # Mark as async generator
        yield None
