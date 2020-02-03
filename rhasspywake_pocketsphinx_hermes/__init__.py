"""Hermes MQTT server for Rhasspy wakeword with pocketsphinx"""
import io
import json
import logging
import subprocess
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

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------


class WakeHermesMqtt:
    """Hermes MQTT server for Rhasspy wakeword with pocketsphinx."""

    def __init__(
        self,
        client,
        keyphrase: str,
        acoustic_model: Path,
        dictionary_path: Path,
        wakeword_id: str = "default",
        keyphrase_threshold: float = 1e-40,
        mllr_matrix: typing.Optional[Path] = None,
        siteIds: typing.Optional[typing.List[str]] = None,
        enabled: bool = True,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        chunk_size: int = 960,
        debug: bool = False,
    ):
        self.client = client

        self.keyphrase = keyphrase
        self.keyphrase_threshold = keyphrase_threshold

        self.acoustic_model = acoustic_model
        self.dictionary_path = dictionary_path
        self.mllr_matrix = mllr_matrix

        self.wakeword_id = wakeword_id
        self.siteIds = siteIds or []
        self.enabled = enabled

        # Required audio format
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels

        self.chunk_size = chunk_size

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
            "Loading decoder with hmm=%s, dict=%s",
            str(self.acoustic_model),
            str(self.dictionary_path),
        )

        decoder_config = pocketsphinx.Decoder.default_config()
        decoder_config.set_string("-hmm", str(self.acoustic_model))
        decoder_config.set_string("-dict", str(self.dictionary_path))
        decoder_config.set_string("-keyphrase", self.keyphrase)
        decoder_config.set_float("-kws_threshold", self.keyphrase_threshold)

        if not self.debug:
            decoder_config.set_string("-logfn", "/dev/null")

        if self.mllr_matrix:
            decoder_config.set_string("-mllr", str(self.mllr_matrix))

        self.decoder = pocketsphinx.Decoder(decoder_config)

    # -------------------------------------------------------------------------

    def handle_audio_frame(
        self, wav_bytes: bytes, siteId: str = "default"
    ) -> typing.Iterable[
        typing.Tuple[str, typing.Union[HotwordDetected, HotwordError]]
    ]:
        """Process a single audio frame"""
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

                yield (self.wakeword_id, self.handle_detection(siteId=siteId))

                # Stop and clear buffer to avoid duplicate reports
                self.audio_buffer = bytes()
                break

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

    # -------------------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        """Connected to MQTT broker."""
        try:
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
                    for wakewordId, result in self.handle_audio_frame(
                        msg.payload, siteId=siteId
                    ):
                        if isinstance(result, HotwordDetected):
                            # Topic contains wake word id
                            self.publish(result, wakewordId=wakewordId)
                        else:
                            self.publish(result)
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

    def _convert_wav(self, wav_data: bytes) -> bytes:
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
            input=wav_data,
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
