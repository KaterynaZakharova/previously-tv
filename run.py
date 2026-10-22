#!/usr/bin/env python3
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['local', 'aws'], default=None)
    parser.add_argument(
        '--aws-engine', choices=['bedrock', 'strands', 'agentcore'], default=None
    )
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    from server import load_env, create_server

    root = Path(__file__).resolve().parent
    load_env(root)
    if args.mode:
        os.environ['RUN_MODE'] = args.mode
    if args.aws_engine:
        os.environ['AWS_AI_ENGINE'] = args.aws_engine
    mode = os.environ.get('RUN_MODE', 'local')
    print(
        'Previously mode='
        + mode
        + (
            ' engine=' + os.environ.get('AWS_AI_ENGINE', 'strands')
            if mode == 'aws'
            else ''
        ),
        flush=True,
    )
    server = create_server(args.host, args.port, root=root)
    print('Open http://localhost:' + str(server.server_port) + '/?tv=1', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
