#!/usr/bin/env python3
"""Parse every XML file under android/ and fail on the first malformed one.

aapt2 catches these too, but only after Gradle has downloaded the toolchain and
the SDK - three minutes in, for something a parser settles instantly. The one
that prompted this: "--" is not allowed inside an XML comment, so a comment
naming a CSS variable took a whole build down.
"""
import glob
import os
import sys
import xml.dom.minidom

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    bad = []
    files = sorted(glob.glob(os.path.join(HERE, "**", "*.xml"), recursive=True))
    for f in files:
        try:
            xml.dom.minidom.parse(f)
        except Exception as e:
            bad.append(f"{os.path.relpath(f, HERE)}: {e}")
    for line in bad:
        print("  malformed:", line)
    print(f"[xml] {len(files) - len(bad)}/{len(files)} parse")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
