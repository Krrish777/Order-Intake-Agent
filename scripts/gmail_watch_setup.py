#!/usr/bin/env python3
"""Create Pub/Sub topic + PULL subscription + grant Gmail publisher role.

Usage:
    uv run python scripts/gmail_watch_setup.py \\
        --project demo-order-intake-local \\
        --topic gmail-inbox-events \\
        --subscription order-intake-ingestion

Idempotent - safe to re-run. Grants gmail-api-push@system.gserviceaccount
.com the pubsub.publisher role on the topic (Gmail requires this to
deliver notifications).

Spec: docs/superpowers/specs/2026-04-24-track-a3-pubsub-ingestion-design.md
"""
from __future__ import annotations

import argparse
import sys

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1


GMAIL_SERVICE_ACCOUNT = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
PUBLISHER_ROLE = "roles/pubsub.publisher"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--subscription", required=True)
    args = parser.parse_args()

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    topic_path = publisher.topic_path(args.project, args.topic)
    subscription_path = subscriber.subscription_path(args.project, args.subscription)

    # 1. Create topic
    try:
        publisher.create_topic(request={"name": topic_path})
        print(f"created topic: {topic_path}")
    except AlreadyExists:
        print(f"topic already exists: {topic_path}")

    # 2. Grant Gmail service account publisher role
    policy = publisher.get_iam_policy(request={"resource": topic_path})
    has_role = any(
        b.role == PUBLISHER_ROLE and GMAIL_SERVICE_ACCOUNT in b.members
        for b in policy.bindings
    )
    if not has_role:
        from google.iam.v1 import policy_pb2
        binding = policy_pb2.Binding(
            role=PUBLISHER_ROLE, members=[GMAIL_SERVICE_ACCOUNT]
        )
        policy.bindings.append(binding)
        publisher.set_iam_policy(
            request={"resource": topic_path, "policy": policy}
        )
        print(f"granted {PUBLISHER_ROLE} to {GMAIL_SERVICE_ACCOUNT}")
    else:
        print(f"{GMAIL_SERVICE_ACCOUNT} already has {PUBLISHER_ROLE}")

    # 3. Create PULL subscription
    try:
        subscriber.create_subscription(
            request={"name": subscription_path, "topic": topic_path}
        )
        print(f"created subscription: {subscription_path}")
    except AlreadyExists:
        print(f"subscription already exists: {subscription_path}")

    print()
    print("Next: set these in .env and run scripts/gmail_pubsub_worker.py")
    print(f"  GMAIL_PUBSUB_PROJECT_ID={args.project}")
    print(f"  GMAIL_PUBSUB_TOPIC={args.topic}")
    print(f"  GMAIL_PUBSUB_SUBSCRIPTION={args.subscription}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
