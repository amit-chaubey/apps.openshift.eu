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

apk add --no-cache \
  bash curl jq netcat-openbsd python3 py3-pip git openssh-client \
  ca-certificates tar gzip unzip bzip2 gcompat libstdc++ aws-cli

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

# mkdocs, in its own virtual environment
python3 -m venv /opt/mkdocs
/opt/mkdocs/bin/pip install --no-cache-dir --quiet mkdocs mkdocs-material
ln -sf /opt/mkdocs/bin/mkdocs "$BIN/mkdocs"

# ssh and git need a passwd entry; init.sh adds one for the arbitrary UID
chmod g=u /etc/passwd
