#!/bin/bash
# Run this once with: sudo bash /home/hnester/file-share/setup.sh
# Re-run anytime to pick up new system users.
set -e

mkdir -p /home/shared-files
chmod 1777 /home/shared-files

# Auto-setup for all real users (UID >= 1000, with a home dir under /home/)
for homedir in /home/*/; do
    user=$(basename "$homedir")

    # Skip special directories
    [ "$user" = "shared-files" ] && continue
    [ "$user" = "lost+found" ] && continue

    # Verify it's a real user account
    id "$user" &>/dev/null || continue

    # Create shared-files folder
    mkdir -p "/home/shared-files/$user"
    chown "$user:$user" "/home/shared-files/$user"
    chmod 755 "/home/shared-files/$user"

    # Symlink their home directory
    if [ ! -e "/home/shared-files/$user/home" ]; then
        ln -s "/home/$user" "/home/shared-files/$user/home"
        echo "  Linked: /home/shared-files/$user/home -> /home/$user"
    fi
done

echo "Done! /home/shared-files is ready."
ls -la /home/shared-files/
