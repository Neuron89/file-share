#!/bin/bash
# Run this once with: sudo bash /home/hnester/file-share/setup.sh
set -e

mkdir -p /home/shared-files
chmod 1777 /home/shared-files

# Create user folders
for user in hnester dtrahan; do
    mkdir -p /home/shared-files/$user
    chown $user:$user /home/shared-files/$user
    chmod 755 /home/shared-files/$user
done

echo "Done! /home/shared-files is ready."
ls -la /home/shared-files/
