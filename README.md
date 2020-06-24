# Rhasspy Wake Pocketsphinx Hermes

Implements `hermes/hotword` functionality from [Hermes protocol](https://docs.snips.ai/reference/hermes) using [Pocketsphinx](https://github.com/cmusphinx/pocketsphinx).

## Requirements

* Python 3.7
* [Pocketsphinx](https://github.com/cmusphinx/pocketsphinx)

## Installation

```bash
$ git clone https://github.com/rhasspy/rhasspy-wake-pocketsphinx-hermes
$ cd rhasspy-wake-pocketsphinx-hermes
$ ./configure
$ make
$ make install
```

## Running

```bash
$ bin/rhasspy-wake-pocketsphinx-hermes <ARGS>
```

## Command-Line Options

```
usage: rhasspy-wake-pocketsphinx-hermes [-h] --acoustic-model ACOUSTIC_MODEL
                                        --dictionary DICTIONARY --keyphrase
                                        KEYPHRASE
                                        [--keyphrase-threshold KEYPHRASE_THRESHOLD]
                                        [--mllr-matrix MLLR_MATRIX]
                                        [--wakeword-id WAKEWORD_ID]
                                        [--udp-audio UDP_AUDIO UDP_AUDIO UDP_AUDIO]
                                        [--host HOST] [--port PORT]
                                        [--username USERNAME]
                                        [--password PASSWORD] [--tls]
                                        [--tls-ca-certs TLS_CA_CERTS]
                                        [--tls-certfile TLS_CERTFILE]
                                        [--tls-keyfile TLS_KEYFILE]
                                        [--tls-cert-reqs {CERT_REQUIRED,CERT_OPTIONAL,CERT_NONE}]
                                        [--tls-version TLS_VERSION]
                                        [--tls-ciphers TLS_CIPHERS]
                                        [--site-id SITE_ID] [--debug]
                                        [--log-format LOG_FORMAT]

optional arguments:
  -h, --help            show this help message and exit
  --acoustic-model ACOUSTIC_MODEL
                        Path to Pocketsphinx acoustic model directory (hmm)
  --dictionary DICTIONARY
                        Path to pronunciation dictionary file(s)
  --keyphrase KEYPHRASE
                        Keyword phrase to listen for
  --keyphrase-threshold KEYPHRASE_THRESHOLD
                        Threshold for keyphrase (default: 1e-40)
  --mllr-matrix MLLR_MATRIX
                        Path to tuned MLLR matrix file
  --wakeword-id WAKEWORD_ID
                        Wakeword ID of each keyphrase (default: use keyphrase)
  --udp-audio UDP_AUDIO UDP_AUDIO UDP_AUDIO
                        Host/port/siteId for UDP audio input
  --host HOST           MQTT host (default: localhost)
  --port PORT           MQTT port (default: 1883)
  --username USERNAME   MQTT username
  --password PASSWORD   MQTT password
  --tls                 Enable MQTT TLS
  --tls-ca-certs TLS_CA_CERTS
                        MQTT TLS Certificate Authority certificate files
  --tls-certfile TLS_CERTFILE
                        MQTT TLS certificate file (PEM)
  --tls-keyfile TLS_KEYFILE
                        MQTT TLS key file (PEM)
  --tls-cert-reqs {CERT_REQUIRED,CERT_OPTIONAL,CERT_NONE}
                        MQTT TLS certificate requirements (default:
                        CERT_REQUIRED)
  --tls-version TLS_VERSION
                        MQTT TLS version (default: highest)
  --tls-ciphers TLS_CIPHERS
                        MQTT TLS ciphers to use
  --site-id SITE_ID     Hermes site id(s) to listen for (default: all)
  --debug               Print DEBUG messages to the console
  --log-format LOG_FORMAT
                        Python logger format
```
