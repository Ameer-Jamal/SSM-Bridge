from __future__ import annotations

import argparse
import json
import sys

from ssm_bridge import SsmBridgeBackend, SsmBridgeError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AWS SSM operations from the terminal.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("targets", help="List configured targets.")
    status_parser = subparsers.add_parser("status", help="Check target status.")
    status_parser.add_argument("--target", default="")
    status_parser.add_argument("--aws-profile", default="")
    status_parser.add_argument("--instance-id", default="")

    find_parser = subparsers.add_parser("find", help="Find EC2 instances by id or Name tag substring.")
    find_parser.add_argument("query")
    find_parser.add_argument("--aws-profile", default="")

    run_parser = subparsers.add_parser("run", help="Run a shell command through AWS SSM.")
    run_parser.add_argument("shell_command")
    run_parser.add_argument("--target", default="")
    run_parser.add_argument("--aws-profile", default="")
    run_parser.add_argument("--instance-id", default="")
    run_parser.add_argument("--timeout-seconds", type=int, default=600)

    get_parser = subparsers.add_parser("get-file", help="Read a small remote file.")
    get_parser.add_argument("remote_path")
    get_parser.add_argument("--target", default="")
    get_parser.add_argument("--aws-profile", default="")
    get_parser.add_argument("--instance-id", default="")
    get_parser.add_argument("--max-bytes", type=int, default=200000)

    upload_parser = subparsers.add_parser("upload", help="Upload a local file through AWS SSM.")
    upload_parser.add_argument("local_path")
    upload_parser.add_argument("remote_path")
    upload_parser.add_argument("--target", default="")
    upload_parser.add_argument("--aws-profile", default="")
    upload_parser.add_argument("--instance-id", default="")

    download_parser = subparsers.add_parser("download", help="Download a remote file through AWS SSM.")
    download_parser.add_argument("remote_path")
    download_parser.add_argument("local_path")
    download_parser.add_argument("--target", default="")
    download_parser.add_argument("--aws-profile", default="")
    download_parser.add_argument("--instance-id", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend = SsmBridgeBackend()

    try:
        if args.command == "targets":
            result = backend.list_targets()
        elif args.command == "status":
            result = backend.status(target=args.target, aws_profile=args.aws_profile, instance_id=args.instance_id)
        elif args.command == "find":
            result = backend.find_instances(query=args.query, aws_profile=args.aws_profile)
        elif args.command == "run":
            result = backend.run_command(
                args.shell_command,
                target=args.target,
                aws_profile=args.aws_profile,
                instance_id=args.instance_id,
                timeout_seconds=args.timeout_seconds,
            )
        elif args.command == "get-file":
            result = backend.get_file(
                args.remote_path,
                target=args.target,
                aws_profile=args.aws_profile,
                instance_id=args.instance_id,
                max_bytes=args.max_bytes,
            )
        elif args.command == "upload":
            result = backend.upload_file(
                args.local_path,
                remote_path=args.remote_path,
                target=args.target,
                aws_profile=args.aws_profile,
                instance_id=args.instance_id,
            )
        elif args.command == "download":
            result = backend.download_file(
                args.remote_path,
                args.local_path,
                target=args.target,
                aws_profile=args.aws_profile,
                instance_id=args.instance_id,
            )
        else:
            raise ValueError(f"unsupported command: {args.command}")
    except (SsmBridgeError, ValueError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
