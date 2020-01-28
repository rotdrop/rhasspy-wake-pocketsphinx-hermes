# Rhasspy Wake Pocketsphinx Hermes

Implements `hermes/hotword` functionality from [Hermes protocol](https://docs.snips.ai/reference/hermes) using [Pocketsphinx](https://github.com/cmusphinx/pocketsphinx).

## Running With Docker

```bash
docker run -it rhasspy/rhasspy-wake-pocketsphinx-hermes:<VERSION> <ARGS>
```

## Building From Source

Clone the repository and create the virtual environment:

```bash
git clone https://github.com/rhasspy/rhasspy-wake-pocketsphinx-hermes.git
cd rhasspy-wake-pocketsphinx-hermes
make venv
```

Run the `bin/rhasspy-wake-pocketsphinx-hermes` script to access the command-line interface:

```bash
bin/rhasspy-wake-pocketsphinx-hermes --help
```

## Building the Debian Package

Follow the instructions to build from source, then run:

```bash
source .venv/bin/activate
make debian
```

If successful, you'll find a `.deb` file in the `dist` directory that can be installed with `apt`.

## Building the Docker Image

Follow the instructions to build from source, then run:

```bash
source .venv/bin/activate
make docker
```

This will create a Docker image tagged `rhasspy/rhasspy-wake-pocketsphinx-hermes:<VERSION>` where `VERSION` comes from the file of the same name in the source root directory.

NOTE: If you add things to the Docker image, make sure to whitelist them in `.dockerignore`.

## Command-Line Options

```
usage: rhasspy-wake-pocketsphinx-hermes [-h] --acoustic-model ACOUSTIC_MODEL
                                        --dictionary DICTIONARY --keyphrase
                                        KEYPHRASE
                                        [--keyphrase-threshold KEYPHRASE_THRESHOLD]
                                        [--mllr-matrix MLLR_MATRIX]
                                        [--wakewordId WAKEWORDID]
                                        [--host HOST] [--port PORT]
                                        [--siteId SITEID] [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --acoustic-model ACOUSTIC_MODEL
                        Path to Pocketsphinx acoustic model directory (hmm)
  --dictionary DICTIONARY
                        Path to pronunciation dictionary file
  --keyphrase KEYPHRASE
                        Keyword phrase to listen for
  --keyphrase-threshold KEYPHRASE_THRESHOLD
                        Threshold for keyphrase (default: 1e-40)
  --mllr-matrix MLLR_MATRIX
                        Path to tuned MLLR matrix file
  --wakewordId WAKEWORDID
                        Wakeword ID of each keyphrase (default: default)
  --host HOST           MQTT host (default: localhost)
  --port PORT           MQTT port (default: 1883)
  --siteId SITEID       Hermes siteId(s) to listen for (default: all)
  --debug               Print DEBUG messages to the console
```
