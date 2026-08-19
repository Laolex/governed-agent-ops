"""Deploy the operations agent to Vertex AI Agent Engine.

Two packaging facts, both of which cost a failed deploy when got wrong:

`extra_packages` paths are relative to the working directory. An absolute path
nests the package inside the archive and the engine dies at startup with
`ModuleNotFoundError`, visible only in Cloud Logging under
`resource.type="aiplatform.googleapis.com/ReasoningEngine"` — not in anything the
SDK raises. So this script must be run from the repository root.

The agent's model is pinned to `global` in ops/agent.py rather than here.
Setting GOOGLE_CLOUD_LOCATION=global on the deployment instead would route the
model correctly and then break memory retrieval silently, because the memory
service would look for this engine in a region where it does not exist.
"""

from __future__ import annotations

import argparse
import os

import vertexai
from vertexai import agent_engines

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "sdl-cinema-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
STAGING = os.environ.get("GAO_STAGING_BUCKET", "gs://sdl-cinema-2026-agent-staging")

REQUIREMENTS = [
    "google-cloud-aiplatform[agent_engines,adk]",
    "google-cloud-firestore",
]


def build_app():
    from ops.agent import build_agent
    from ops.store import FirestoreFleetStore

    return agent_engines.AdkApp(
        agent=build_agent(FirestoreFleetStore()), enable_tracing=True
    )


# GEMINI_LOCATION has to travel with the deployment: ops.agent reads it at import
# time and the deployed container does not inherit this shell's environment. It
# belongs on create/update, not on AdkApp.
ENV_VARS = {"GEMINI_LOCATION": os.environ.get("GEMINI_LOCATION", "global")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", metavar="RESOURCE")
    parser.add_argument("--delete", metavar="RESOURCE")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=STAGING)

    if args.list:
        for engine in agent_engines.list():
            print(engine.resource_name, "|", engine.display_name)
        return

    if args.delete:
        agent_engines.delete(args.delete, force=True)
        print("deleted", args.delete)
        return

    if args.update:
        engine = agent_engines.update(
            resource_name=args.update, agent_engine=build_app(),
            requirements=REQUIREMENTS, extra_packages=["ops"], env_vars=ENV_VARS,
        )
        print("updated", engine.resource_name)
        return

    engine = agent_engines.create(
        agent_engine=build_app(),
        requirements=REQUIREMENTS,
        extra_packages=["ops"],
        env_vars=ENV_VARS,
        display_name="Fleet operations agent",
        description="Resolves fleet references and proposes lifecycle operations.",
    )
    print("RESOURCE", engine.resource_name)


if __name__ == "__main__":
    main()
