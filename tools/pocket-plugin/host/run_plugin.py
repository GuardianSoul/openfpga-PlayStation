#!/usr/bin/env python3
"""Run a Pocket plugin (plugin.wasm) headless, the way Pocket Sync and
pupdate do: the SD card folder is the plugin's pocket/, each Choice is
answered from --answer in order, and the plugin's log goes to stdout.
Needs the extism Python host (python3 -m pip install extism).

  python3 tools/pocket-plugin/host/run_plugin.py plugin.wasm /path/to/sd-card --answer "Make game files"
"""
import argparse
import json
from pathlib import Path
import sys

import extism

LOG = []


@extism.host_fn(name='print_msg', signature=([extism.ValType.I64], [extism.ValType.I64]))
def print_msg(plugin, inputs, outputs):
    text = plugin.input_string(inputs[0])
    LOG.append(text)
    sys.stdout.write(text)
    sys.stdout.flush()


@extism.host_fn(name='open_url', signature=([extism.ValType.I64], [extism.ValType.I64]))
def open_url(plugin, inputs, outputs):
    print(f'[open_url {plugin.input_string(inputs[0])}]')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('wasm', type=Path)
    parser.add_argument('card', type=Path)
    parser.add_argument('--answer', action='append', default=[], help='choice to pick, in order (default: the first);'
                        ' a choice is matched by its start')
    parser.add_argument('--host-folder', type=Path, help="the plugin's own folder on the computer (the plugin's host/)")
    parser.add_argument('--config', action='append', default=[], metavar='KEY=VALUE', help='plugin config (tests)')
    args = parser.parse_args()
    paths = {str(args.card.resolve()): 'pocket'}
    if args.host_folder: paths[str(args.host_folder.resolve())] = 'host'
    manifest = {'wasm': [{'path': str(args.wasm.resolve())}], 'allowed_paths': paths,
                'config': dict(c.split('=', 1) for c in args.config)}
    answers = list(args.answer)
    with extism.Plugin(manifest, wasi=True, functions=[print_msg, open_url]) as plugin:
        message = json.loads(plugin.call('start', b''))
        while message != 'Exit' and 'Exit' not in message:
            (kind, body), = message.items()
            if kind != 'Choice': raise SystemExit(f'unexpected {kind} message: {body}')
            want = answers.pop(0) if answers else body['choices'][0]
            value = next((c for c in body['choices'] if c.startswith(want)), None)
            if value is None: raise SystemExit(f'{want!r} is not one of {body["choices"]}')
            print('  choices: ' + ' | '.join(body['choices']))
            print(f'[{body["query"]}: {value}]')
            reply = {'Answer': {'name': body['name'], 'value': value}}
            message = json.loads(plugin.call('handle_response', json.dumps(reply).encode()))


if __name__ == '__main__':
    main()
