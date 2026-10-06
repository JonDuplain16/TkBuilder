"""A small script for the Script Runner example: prints progress slowly."""
import sys
import time

steps = int(sys.argv[1]) if len(sys.argv) > 1 else 8
print("Starting a slow task with %d steps..." % steps)
for i in range(1, steps + 1):
    time.sleep(0.4)
    print("  step %d of %d done" % (i, steps))
print("All done!")
