#!/bin/sh
# Fails the image build when any tool does not run; called as a non-root UID.
set -eu
export HOME=/tmp KREW_ROOT=/tmp/.krew
# Same passwd entry init.sh writes for the arbitrary UID of the pod
id -un >/dev/null 2>&1 || echo "agent:x:$(id -u):0:agent:$HOME:/bin/sh" >> /etc/passwd
failed=""
check() {
  if out=$("$@" 2>&1); then
    printf '%-14s %s\n' "$1" "$(printf '%s' "$out" | grep -m1 -v '^\s*$' | cut -c1-100)"
  else
    printf '%-14s FAILED: %s\n' "$1" "$(printf '%s' "$out" | head -3 | tr '\n' ' ' | cut -c1-200)"
    failed="$failed $1"
  fi
}
# clusteradm version also asks a cluster; only its client line has to be there
clusteradm_version() { clusteradm version 2>&1 | grep -i version; }
check curl --version
check jq --version
check yq --version
check bash --version
check nc -h
check python3 --version
check git --version
check ssh -V
check oc version --client
check kubectl version --client
check argocd version --client
check kn version
check ccoctl --help
check hcp version
check virtctl version --client
check amtool --version
check kubectl-cnpg version
check kubectl-mariadb --help
check kubectl mdba --help
check helm version
check kustomize version
check glab version
check clusteradm_version
check velero version --client-only
check restic version
check odf --help
check operator-sdk version
check kubectl-krew version
check aws --version
check mkdocs --version
check bw --version
check node --version
check npm --version
check npx --version
check uv --version
check uvx --version
check make --version
check go version
check terraform version
check packer version
check ansible --version
check ansible-galaxy collection list kubernetes.core
check hf version
check fastapi --version
check uvicorn --version
check gh --version
check promtool --version
check gitleaks version
check rclone version
check opa version
check conftest --version
check trivy --version
check grype version
check syft version
check kube-linter version
check hadolint --version
check shellcheck --version
check kubeconform -v
check cosign version
check semgrep --version
check checkov --version
check sonar-scanner --version
check renovate --version
check snyk --version
check tmux -V
check psql --version
check mariadb --version
check openssl version
check dig -v
check python3 -c 'import yaml; print("pyyaml", yaml.__version__)'
check opencode --version
check /tools/kubernetes-mcp-server --version
# Leave passwd as the build found it; the pod adds its own entry
grep -v "^agent:x:$(id -u):" /etc/passwd > /tmp/passwd && cat /tmp/passwd > /etc/passwd
echo "image size: $(du -shx / 2>/dev/null | cut -f1)"
if [ -n "$failed" ]; then
  echo "tools that do not run:$failed" >&2
  exit 1
fi
