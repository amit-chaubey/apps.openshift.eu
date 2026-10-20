#!/bin/sh
# Installs the agent toolset into /usr/local/bin at the latest upstream release.
# Runs at image build time on Alpine (musl); glibc-only binaries use gcompat.
set -eu

# The build context comes from a ConfigMap; the check runs as another user
chmod 0755 "$(dirname "$0")"/*.sh

BIN=/usr/local/bin
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

fetch() { curl -fsSL --retry 5 --retry-delay 3 -o "$2" "$1"; }

# Tag of the release GitHub marks latest, read from the redirect (no API quota)
latest() {
  curl -fsSLI --retry 5 -o /dev/null -w '%{url_effective}' "https://github.com/$1/releases/latest" | sed 's#.*/tag/##'
}

gh_release() { echo "https://github.com/$1/releases/download/$2/$3"; }

# Extracts the named members of a tarball into BIN
untar() {
  archive=$1; shift
  tar -xzf "$archive" -C "$WORK" "$@"
  for member in "$@"; do install -m 0755 "$WORK/$member" "$BIN/$(basename "$member")"; done
}

# Alpine packages: shell basics, MCP runtimes (node, python), clients engineers use
apk add --no-cache \
  bash curl jq netcat-openbsd python3 py3-pip py3-yaml git openssh-client \
  ca-certificates tar gzip unzip bzip2 xz zstd gcompat libstdc++ aws-cli \
  nodejs npm make tmux ncurses openssl bind-tools rsync tree less \
  postgresql-client mariadb-client

# uv and uvx, static musl build
TAG=$(latest astral-sh/uv)
fetch "$(gh_release astral-sh/uv "$TAG" uv-x86_64-unknown-linux-musl.tar.gz)" uv.tgz
untar uv.tgz uv-x86_64-unknown-linux-musl/uv uv-x86_64-unknown-linux-musl/uvx

# Python CLIs, each in its own environment under /opt/uv-tools
export UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR="$BIN" UV_PYTHON_PREFERENCE=only-system UV_NO_CACHE=1
uv tool install mkdocs --with mkdocs-material
uv tool install ansible-core
uv tool install "huggingface_hub[cli]"
uv tool install "fastapi[standard]"
uv tool install uvicorn
# Collections used across the platform, readable by every user
ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections \
  ansible-galaxy collection install --no-cache kubernetes.core community.general ansible.posix community.crypto
chmod -R a+rX /opt/uv-tools /usr/share/ansible

# go, newest stable toolchain from go.dev
GO=$(curl -fsSL 'https://go.dev/dl/?mode=json' | jq -r '[.[] | select(.stable)][0].version')
fetch "https://go.dev/dl/$GO.linux-amd64.tar.gz" go.tgz
tar -xzf go.tgz -C /usr/local
ln -sf /usr/local/go/bin/go /usr/local/go/bin/gofmt "$BIN/"

# terraform and packer, newest release from HashiCorp
for tool in terraform packer; do
  V=$(curl -fsSL "https://checkpoint-api.hashicorp.com/v1/check/$tool" | jq -r .current_version)
  fetch "https://releases.hashicorp.com/$tool/$V/${tool}_${V}_linux_amd64.zip" "$tool.zip"
  unzip -qo "$tool.zip" "$tool"
  install -m 0755 "$tool" "$BIN/$tool"
done

# gh
TAG=$(latest cli/cli)
fetch "$(gh_release cli/cli "$TAG" "gh_${TAG#v}_linux_amd64.tar.gz")" gh.tgz
untar gh.tgz "gh_${TAG#v}_linux_amd64/bin/gh"

# promtool, for alert rule checks
TAG=$(latest prometheus/prometheus)
fetch "$(gh_release prometheus/prometheus "$TAG" "prometheus-${TAG#v}.linux-amd64.tar.gz")" prom.tgz
untar prom.tgz "prometheus-${TAG#v}.linux-amd64/promtool"

# gitleaks
TAG=$(latest gitleaks/gitleaks)
fetch "$(gh_release gitleaks/gitleaks "$TAG" "gitleaks_${TAG#v}_linux_x64.tar.gz")" gitleaks.tgz
untar gitleaks.tgz gitleaks

# rclone
fetch https://downloads.rclone.org/rclone-current-linux-amd64.zip rclone.zip
unzip -qo rclone.zip
install -m 0755 rclone-*-linux-amd64/rclone "$BIN/rclone"

# yq
fetch "$(gh_release mikefarah/yq "$(latest mikefarah/yq)" yq_linux_amd64)" "$BIN/yq"
chmod 0755 "$BIN/yq"

# oc and kubectl, OpenShift stable channel
fetch https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz oc.tgz
untar oc.tgz oc kubectl

# ccoctl, OpenShift stable channel
fetch https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/ccoctl-linux.tar.gz ccoctl.tgz
untar ccoctl.tgz ccoctl

# hcp, newest multicluster engine client on the Red Hat content gateway
HCP_BASE=https://developers.redhat.com/content-gateway
HCP=$(curl -fsSL "$HCP_BASE/rest/browse/pub/mce/clients/hcp-cli/" \
  | grep -oE 'hcp-cli/[0-9][^/"]+/' | sort -uV | tail -1 | cut -d/ -f2)
fetch "$HCP_BASE/file/pub/mce/clients/hcp-cli/$HCP/hcp-cli-$HCP-linux-amd64.tar.gz" hcp.tgz
tar -xzf hcp.tgz
install -m 0755 "$(find . -type f -name hcp | head -1)" "$BIN/hcp"

# argocd
fetch "$(gh_release argoproj/argo-cd "$(latest argoproj/argo-cd)" argocd-linux-amd64)" "$BIN/argocd"
chmod 0755 "$BIN/argocd"

# kn
fetch "$(gh_release knative/client "$(latest knative/client)" kn-linux-amd64)" "$BIN/kn"
chmod 0755 "$BIN/kn"

# virtctl
TAG=$(latest kubevirt/kubevirt)
fetch "$(gh_release kubevirt/kubevirt "$TAG" "virtctl-$TAG-linux-amd64")" "$BIN/virtctl"
chmod 0755 "$BIN/virtctl"

# amtool
TAG=$(latest prometheus/alertmanager)
fetch "$(gh_release prometheus/alertmanager "$TAG" "alertmanager-${TAG#v}.linux-amd64.tar.gz")" am.tgz
untar am.tgz "alertmanager-${TAG#v}.linux-amd64/amtool"

# kubectl-cnpg
TAG=$(latest cloudnative-pg/cloudnative-pg)
fetch "$(gh_release cloudnative-pg/cloudnative-pg "$TAG" "kubectl-cnpg_${TAG#v}_linux_x86_64.tar.gz")" cnpg.tgz
untar cnpg.tgz kubectl-cnpg

# kubectl-mariadb: the mariadb-operator binary of the newest operator release
# (its "latest" release is a chart, so pick the newest plain version tag)
MDB=$(curl -fsSL 'https://api.github.com/repos/mariadb-operator/mariadb-operator/releases?per_page=30' \
  | jq -r '[.[] | select(.tag_name | test("^[0-9]+\\.[0-9]+\\.[0-9]+$"))][0].tag_name')
fetch "$(gh_release mariadb-operator/mariadb-operator "$MDB" "mariadb-operator_${MDB}_linux_amd64.tar.gz")" mdb.tgz
mkdir -p mdb && tar -xzf mdb.tgz -C mdb
install -m 0755 "$(find mdb -type f -name mariadb-operator | head -1)" "$BIN/kubectl-mariadb"

# kubectl-mdba, the version the krew index publishes
MDBA_URL=$(curl -fsSL https://raw.githubusercontent.com/kubernetes-sigs/krew-index/master/plugins/mdba.yaml \
  | sed -n 's/^ *uri: *//p' | head -1)
fetch "$MDBA_URL" mdba.tgz
mkdir -p mdba && tar -xzf mdba.tgz -C mdba
install -m 0755 "$(find mdba -type f -name kubectl-mdba | head -1)" "$BIN/kubectl-mdba"

# helm
fetch "https://get.helm.sh/helm-$(latest helm/helm)-linux-amd64.tar.gz" helm.tgz
untar helm.tgz linux-amd64/helm

# kustomize
TAG=$(latest kubernetes-sigs/kustomize)
fetch "$(gh_release kubernetes-sigs/kustomize "$TAG" "kustomize_${TAG#kustomize/}_linux_amd64.tar.gz")" kustomize.tgz
untar kustomize.tgz kustomize

# glab
GLAB=$(curl -fsSL 'https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases/permalink/latest' | jq -r .tag_name)
fetch "https://gitlab.com/gitlab-org/cli/-/releases/$GLAB/downloads/glab_${GLAB#v}_linux_amd64.tar.gz" glab.tgz
untar glab.tgz bin/glab

# clusteradm
fetch "$(gh_release open-cluster-management-io/clusteradm "$(latest open-cluster-management-io/clusteradm)" clusteradm_linux_amd64.tar.gz)" clusteradm.tgz
untar clusteradm.tgz clusteradm

# velero
TAG=$(latest vmware-tanzu/velero)
fetch "$(gh_release vmware-tanzu/velero "$TAG" "velero-$TAG-linux-amd64.tar.gz")" velero.tgz
untar velero.tgz "velero-$TAG-linux-amd64/velero"

# restic
TAG=$(latest restic/restic)
fetch "$(gh_release restic/restic "$TAG" "restic_${TAG#v}_linux_amd64.bz2")" restic.bz2
bunzip2 -c restic.bz2 > "$BIN/restic"
chmod 0755 "$BIN/restic"

# operator-sdk
fetch "$(gh_release operator-framework/operator-sdk "$(latest operator-framework/operator-sdk)" operator-sdk_linux_amd64)" "$BIN/operator-sdk"
chmod 0755 "$BIN/operator-sdk"

# krew, as the kubectl plugin binary; plugins land under KREW_ROOT (HOME)
fetch "$(gh_release kubernetes-sigs/krew "$(latest kubernetes-sigs/krew)" krew-linux_amd64.tar.gz)" krew.tgz
tar -xzf krew.tgz ./krew-linux_amd64
install -m 0755 krew-linux_amd64 "$BIN/kubectl-krew"

# bw from npm: the release zip is a glibc binary that gcompat cannot run (fcntl64)
apk add --no-cache nodejs npm
npm install --global --no-fund --no-audit --omit=dev @bitwarden/cli@latest
npm cache clean --force


# ssh and git need a passwd entry; init.sh adds one for the arbitrary UID
chmod g=u /etc/passwd
