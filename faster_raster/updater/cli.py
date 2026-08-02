from __future__ import annotations

from argparse import Namespace

from .models import UpdateChannel
from .service import check, status


def command_update(args: Namespace) -> int:
    if args.update_command == "status":
        result = status()
    else:
        result = check(
            channel=UpdateChannel(args.channel) if args.channel else None,
            allow_network=args.allow_network,
        )
    payload = result.as_dict()
    if args.json:
        import json
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{payload['status']}: channel={payload['channel']} version={payload['installation']['active_version']}")
        if payload.get("error"):
            print(payload["error"])
        print(payload["recommendation"].get("guidance", payload["recommendation"].get("reason", "No action recommended.")))
    return 0 if result.status not in {"error", "blocked"} else 2
