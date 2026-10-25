#!/usr/bin/env python3
"""Let the Claude ACP adapter run against a gateway that sends no token usage.

An OpenAI-compatible backend behind an Anthropic-format gateway streams message_delta
and result messages without usage. The adapter reads usage fields unguarded, its
query stream dies with "Cannot read properties of undefined (reading
'input_tokens')" and every later prompt answers "The Claude Agent session has
ended". Missing usage counts as zero. Fails the build when the adapter no longer has
the expected shape.
"""
import glob
import re
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/acp-node/lib/node_modules/@agentclientprotocol/claude-agent-acp"
FIXES = [
    # the message_delta handler
    (r"const usage = message\.event\.usage;", "const usage = message.event.usage ?? {};"),
    (r"output_tokens: usage\.output_tokens,", "output_tokens: usage.output_tokens ?? prev.output_tokens,"),
    # result messages: message.usage.<field> -> (message.usage?.<field> ?? 0)
    (r"\bmessage\.usage\.(\w+)", r"(message.usage?.\1 ?? 0)"),
]

files = glob.glob(f"{ROOT}/dist/acp-agent.js")
if len(files) != 1:
    sys.exit(f"patch-acp-usage: {ROOT}: {len(files)} files")
src = open(files[0]).read()
for pattern, repl in FIXES:
    src, count = re.subn(pattern, repl, src)
    if count == 0:
        sys.exit(f"patch-acp-usage: {pattern}: no match")
    print(f"patch-acp-usage: {pattern}: {count} changes")
open(files[0], "w").write(src)
