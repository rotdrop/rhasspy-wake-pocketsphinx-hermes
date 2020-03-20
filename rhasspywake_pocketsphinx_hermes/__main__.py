"""Hermes MQTT service for Rhasspy wakeword with pocketsphinx"""
import argparse
import asyncio
import logging
from pathlib import Path

import paho.mqtt.client as mqtt

from . import WakeHermesMqtt

_LOGGER = logging.getLogger("rhasspywake_pocketsphinx_hermes")

# -----------------------------------------------------------------------------


def main():
    """Main method."""
    parser = argparse.ArgumentParser(prog="rhasspy-wake-pocketsphinx-hermes")
    parser.add_argument(
        "--acoustic-model",
        required=True,
        help="Path to Pocketsphinx acoustic model directory (hmm)",
    )
    parser.add_argument(
        "--dictionary",
        required=True,
        action="append",
        help="Path to pronunciation dictionary file(s)",
    )
    parser.add_argument(
        "--keyphrase", required=True, help="Keyword phrase to listen for"
    )
    parser.add_argument(
        "--keyphrase-threshold",
        type=float,
        default=1e-40,
        help="Threshold for keyphrase (default: 1e-40)",
    )
    parser.add_argument(
        "--mllr-matrix", default=None, help="Path to tuned MLLR matrix file"
    )
    parser.add_argument(
        "--wakewordId",
        default="default",
        help="Wakeword ID of each keyphrase (default: default)",
    )
    parser.add_argument(
        "--udp-audio-port", type=int, help="Also listen for WAV audio on UDP"
    )
    parser.add_argument(
        "--host", default="localhost", help="MQTT host (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=1883, help="MQTT port (default: 1883)"
    )
    parser.add_argument(
        "--siteId",
        action="append",
        help="Hermes siteId(s) to listen for (default: all)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to the console"
    )
    parser.add_argument(
        "--log-format",
        default="[%(levelname)s:%(asctime)s] %(name)s: %(message)s",
        help="Python logger format",
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format=args.log_format)
    else:
        logging.basicConfig(level=logging.INFO, format=args.log_format)

    _LOGGER.debug(args)

    try:
        # Convert to paths
        args.acoustic_model = Path(args.acoustic_model)
        args.dictionary = [Path(d) for d in args.dictionary]

        if args.mllr_matrix:
            args.mllr_matrix = Path(args.mllr_matrix)

        loop = asyncio.get_event_loop()

        # Listen for messages
        client = mqtt.Client()
        hermes = WakeHermesMqtt(
            client,
            args.keyphrase,
            args.acoustic_model,
            args.dictionary,
            wakeword_id=args.wakewordId,
            keyphrase_threshold=args.keyphrase_threshold,
            mllr_matrix=args.mllr_matrix,
            udp_audio_port=args.udp_audio_port,
            siteIds=args.siteId,
            debug=args.debug,
            loop=loop,
        )

        hermes.load_decoder()

        _LOGGER.debug("Connecting to %s:%s", args.host, args.port)
        client.connect(args.host, args.port)
        client.loop_start()

        # Run event loop
        hermes.loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _LOGGER.debug("Shutting down")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
