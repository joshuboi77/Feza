#!/bin/bash
# Helper script to create "binary" wrappers for Python CLI tools
# Usage: ./create_python_binaries.sh <name> <module_path>
# Example: ./create_python_binaries.sh feza feza.main

NAME="${1:-feza}"
MODULE="${2:-feza.main}"
TARGET_DIR="build"

mkdir -p "${TARGET_DIR}/macos-arm64"
mkdir -p "${TARGET_DIR}/macos-amd64"
mkdir -p "${TARGET_DIR}/linux-amd64"

# Create wrapper scripts for each target
cat > "${TARGET_DIR}/macos-arm64/${NAME}" << 'EOF'
#!/usr/bin/env python3
import sys
from feza.main import main
if __name__ == "__main__":
    sys.exit(main())
EOF

cat > "${TARGET_DIR}/macos-amd64/${NAME}" << 'EOF'
#!/usr/bin/env python3
import sys
from feza.main import main
if __name__ == "__main__":
    sys.exit(main())
EOF

cat > "${TARGET_DIR}/linux-amd64/${NAME}" << 'EOF'
#!/usr/bin/env python3
import sys
from feza.main import main
if __name__ == "__main__":
    sys.exit(main())
EOF

# Make them executable
chmod +x "${TARGET_DIR}/macos-arm64/${NAME}"
chmod +x "${TARGET_DIR}/macos-amd64/${NAME}"
chmod +x "${TARGET_DIR}/linux-amd64/${NAME}"

echo "Created wrapper scripts for ${NAME} in ${TARGET_DIR}/"
echo "These will be packaged by Feza as 'binaries'"

