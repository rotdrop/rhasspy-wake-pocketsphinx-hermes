"""Hermes MQTT server for Rhasspy wakeword with pocketsphinx"""
import io
import json
import logging
import queue
import socket
import subprocess
import tempfile
import threading
import typing
import wave
from pathlib import Path

import attr
import pocketsphinx
from rhasspyhermes.audioserver import AudioFrame
from rhasspyhermes.base import Message
from rhasspyhermes.wake import (
    HotwordDetected,
    HotwordError,
    HotwordToggleOff,
    HotwordToggleOn,
)

WAV_HEADER_BYTES = 44
_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------


class WakeHermesMqtt:
    """Hermes MQTT server for Rhasspy wakeword with pocketsphinx."""

    def __init__(
        self,
        client,
        keyphrase: str,
        acoustic_model: Path,
        dictionary_paths: typing.List[Path],
        wakeword_id: str = "default",
        keyphrase_threshold: float = 1e-40,
        mllr_matrix: typing.Optional[Path] = None,
        siteIds: typing.Optional[typing.List[str]] = None,
        enabled: bool = True,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        chunk_size: int = 960,
        udp_audio_port: typing.Optional[int] = None,
        udp_chunk_size: int = 2048,
        debug: bool = False,
    ):
        self.client = client

        self.keyphrase = keyphrase
        self.keyphrase_threshold = keyphrase_threshold

        self.acoustic_model = acoustic_model
        self.dictionary_paths = dictionary_paths
        self.mllr_matrix = mllr_matrix

        self.wakeword_id = wakeword_id
        self.siteIds = siteIds or []
        self.enabled = enabled

        # Required audio format
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels

        self.chunk_size = chunk_size

        # Listen for raw audio on UDP too
        self.udp_audio_port = udp_audio_port
        self.udp_chunk_size = udp_chunk_size

        # siteId used for detections from UDP
        self.udp_siteId = "default" if not self.siteIds else self.siteIds[0]

        # Queue of WAV audio chunks to process (plus siteId)
        self.wav_queue: queue.Queue = queue.Queue()

        # Topics to listen for WAV chunks on
        self.audioframe_topics: typing.List[str] = []
        for siteId in self.siteIds:
            self.audioframe_topics.append(AudioFrame.topic(siteId=siteId))

        self.first_audio: bool = True

        self.audio_buffer = bytes()

        self.decoder: typing.Optional[pocketsphinx.Decoder] = []
        self.decoder_started = False
        self.debug = debug

    # -------------------------------------------------------------------------

    def load_decoder(self):
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

            self.decoder = pocketsphinx.Decoder(decoder_config)

    # -------------------------------------------------------------------------

    def handle_audio_frame(self, wav_bytes: bytes, siteId: str = "default"):
        """Process a single audio frame"""

    def handle_detection(
        self, siteId="default"
    ) -> typing.Union[HotwordDetected, HotwordError]:
        """Handle a successful hotword detection"""
        try:
            return HotwordDetected(
                siteId=siteId,
                modelId=self.keyphrase,
                currentSensitivity=self.keyphrase_threshold,
                modelVersion="",
                modelType="personal",
            )
        except Exception as e:
            _LOGGER.exception("handle_detection")
            return HotwordError(error=str(e), context=self.keyphrase, siteId=siteId)

    def detection_thread_proc(self):
        """Handle WAV audio chunks."""
        try:
            while True:
                wav_bytes, siteId = self.wav_queue.get()

                if not self.decoder:
                    self.load_decoder()

                assert self.decoder is not None
                if not self.decoder_started:
                    self.decoder.start_utt()
                    self.decoder_started = True

                # Extract/convert audio data
                audio_data = self.maybe_convert_wav(wav_bytes)

                # Add to persistent buffer
                self.audio_buffer += audio_data

                # Process in chunks.
                # Any remaining audio data will be kept in buffer.
                while len(self.audio_buffer) >= self.chunk_size:
                    chunk = self.audio_buffer[: self.chunk_size]
                    self.audio_buffer = self.audio_buffer[self.chunk_size :]

                    self.decoder.process_raw(chunk, False, False)
                    hyp = self.decoder.hyp()
                    if hyp:
                        if self.decoder_started:
                            self.decoder.end_utt()
                            self.decoder_started = False

                        message = self.handle_detection(siteId=siteId)
                        self.publish(message, wakewordId=self.wakeword_id)

                        # Stop and clear buffer to avoid duplicate reports
                        self.audio_buffer = bytes()
                        break

        except Exception:
            _LOGGER.exception("detection_thread_proc")

    # -------------------------------------------------------------------------

    def udp_thread_proc(self):
        """Handle WAV chunks from UDP socket."""
        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.bind(("127.0.0.1", self.udp_audio_port))
            _LOGGER.debug("Listening for audio on UDP port %s", self.udp_audio_port)

            while True:
                wav_bytes, _ = udp_socket.recvfrom(
                    self.udp_chunk_size + WAV_HEADER_BYTES
                )
                self.wav_queue.put((wav_bytes, self.udp_siteId))
        except Exception:
            _LOGGER.exception("udp_thread_proc")

    # -------------------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        """Connected to MQTT broker."""
        try:
            # Start threads
            threading.Thread(target=self.detection_thread_proc, daemon=True).start()

            if self.udp_audio_port is not None:
                threading.Thread(target=self.udp_thread_proc, daemon=True).start()

            topics = [HotwordToggleOn.topic(), HotwordToggleOff.topic()]

            if self.audioframe_topics:
                # Specific siteIds
                topics.extend(self.audioframe_topics)
            else:
                # All siteIds
                topics.append(AudioFrame.topic(siteId="+"))

            for topic in topics:
                self.client.subscribe(topic)
                _LOGGER.debug("Subscribed to %s", topic)
        except Exception:
            _LOGGER.exception("on_connect")

    def on_message(self, client, userdata, msg):
        """Received message from MQTT broker."""
        try:
            if not msg.topic.endswith("/audioFrame"):
                _LOGGER.debug("Received %s byte(s) on %s", len(msg.payload), msg.topic)

            # Check enable/disable messages
            if msg.topic == HotwordToggleOn.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.enabled = True
                    self.first_audio = True
                    _LOGGER.debug("Enabled")
            elif msg.topic == HotwordToggleOff.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.enabled = False
                    _LOGGER.debug("Disabled")

            if not self.enabled:
                # Disabled
                return

            # Handle audio frames
            if AudioFrame.is_topic(msg.topic):
                if (not self.audioframe_topics) or (
                    msg.topic in self.audioframe_topics
                ):
                    if self.first_audio:
                        _LOGGER.debug("Receiving audio")
                        self.first_audio = False

                    siteId = AudioFrame.get_siteId(msg.topic)
                    self.handle_audio_frame(msg.payload, siteId=siteId)
        except Exception:
            _LOGGER.exception("on_message")

    def publish(self, message: Message, **topic_args):
        """Publish a Hermes message to MQTT."""
        try:
            _LOGGER.debug("-> %s", message)
            topic = message.topic(**topic_args)
            payload = json.dumps(attr.asdict(message))
            _LOGGER.debug("Publishing %s char(s) to %s", len(payload), topic)
            self.client.publish(topic, payload)
        except Exception:
            _LOGGER.exception("on_message")

    # -------------------------------------------------------------------------

    def _check_siteId(self, json_payload: typing.Dict[str, typing.Any]) -> bool:
        if self.siteIds:
            return json_payload.get("siteId", "default") in self.siteIds

        # All sites
        return True

    # -------------------------------------------------------------------------

    def _convert_wav(self, wav_bytes: bytes) -> bytes:
        """Converts WAV data to required format with sox. Return raw audio."""
        return subprocess.run(
            [
                "sox",
                "-t",
                "wav",
                "-",
                "-r",
                str(self.sample_rate),
                "-e",
                "signed-integer",
                "-b",
                str(self.sample_width * 8),
                "-c",
                str(self.channels),
                "-t",
                "raw",
                "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
            input=wav_bytes,
        ).stdout

    def maybe_convert_wav(self, wav_bytes: bytes) -> bytes:
        """Converts WAV data to required format if necessary. Returns raw audio."""
        with io.BytesIO(wav_bytes) as wav_io:
            with wave.open(wav_io, "rb") as wav_file:
                if (
                    (wav_file.getframerate() != self.sample_rate)
                    or (wav_file.getsampwidth() != self.sample_width)
                    or (wav_file.getnchannels() != self.channels)
                ):
                    # Return converted wav
                    return self._convert_wav(wav_bytes)

                # Return original audio
                return wav_file.readframes(wav_file.getnframes())
