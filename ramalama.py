#!/usr/bin/python3

import os
import glob
import sys
import subprocess
import json
import hashlib
import shutil
import time
import re
import logging
from pathlib import Path

x = False
funcDict = {}


def verify_checksum(filename):
    """
    Verifies if the SHA-256 checksum of a file matches the checksum provided in
    the filename.

    Args:
    filename (str): The filename containing the checksum prefix
                    (e.g., "sha256:<checksum>")

    Returns:
    bool: True if the checksum matches, False otherwise.
    """

    if not os.path.exists(filename):
        return False

    # Check if the filename starts with "sha256:"
    fn_base = os.path.basename(filename)
    if not fn_base.startswith("sha256:"):
        raise ValueError(f"Filename does not start with 'sha256:': {fn_base}")

    # Extract the expected checksum from the filename
    expected_checksum = fn_base.split(":")[1]
    if len(expected_checksum) != 64:
        raise ValueError("Invalid checksum length in filename")

    # Calculate the SHA-256 checksum of the file contents
    sha256_hash = hashlib.sha256()
    with open(filename, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)

    # Compare the checksums
    return sha256_hash.hexdigest() == expected_checksum


def print_error(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def run_cmd(args, cwd=None):
    if x:
        print(*args)

    return subprocess.run(args, check=True, cwd=cwd, stdout=subprocess.PIPE)


def exec_cmd(args):
    if x:
        print(*args)

    return os.execvp(args[0], args)


def run_curl_cmd(args, filename):
    if not verify_checksum(filename):
        try:
            run_cmd(args)
        except subprocess.CalledProcessError as e:
            if e.returncode == 22:
                print_error(filename + " not found")
            sys.exit(e.returncode)


def pull_ollama_manifest(repos_ollama, manifests, accept, registry_head, model_tag):
    os.makedirs(os.path.dirname(manifests), exist_ok=True)
    os.makedirs(os.path.join(repos_ollama, "blobs"), exist_ok=True)
    curl_cmd = [
        "curl", "-f", "-s", "--header", accept,
        "-o", manifests,
        f"{registry_head}/manifests/{model_tag}"
    ]
    run_cmd(curl_cmd)


def pull_ollama_config_blob(repos_ollama, accept, registry_head, manifest_data):
    cfg_hash = manifest_data["config"]["digest"]
    config_blob_path = os.path.join(repos_ollama, "blobs", cfg_hash)
    curl_cmd = [
        "curl", "-f", "-s", "-L", "-C", "-", "--header", accept,
        "-o", config_blob_path,
        f"{registry_head}/blobs/{cfg_hash}"
    ]
    run_curl_cmd(curl_cmd, config_blob_path)


def pull_ollama_blob(repos_ollama, layer_digest, accept, registry_head, ramalama_models, model_name, model_tag, symlink_path):
    layer_blob_path = os.path.join(repos_ollama, "blobs", layer_digest)
    curl_cmd = ["curl", "-f", "-L", "-C", "-", "--progress-bar", "--header",
                accept, "-o", layer_blob_path,
                f"{registry_head}/blobs/{layer_digest}"]
    run_curl_cmd(curl_cmd, layer_blob_path)
    os.makedirs(ramalama_models, exist_ok=True)
    relative_target_path = os.path.relpath(
        layer_blob_path, start=os.path.dirname(symlink_path))
    try:
        run_cmd(["ln", "-sf", relative_target_path, symlink_path])
    except subprocess.CalledProcessError as e:
        print_error(e)
        sys.exit(e.returncode)


def init_pull(repos_ollama, manifests, accept, registry_head, model_name, model_tag, ramalama_models, symlink_path, model):
    try:
        pull_ollama_manifest(repos_ollama, manifests,
                             accept, registry_head, model_tag)
        with open(manifests, 'r') as f:
            manifest_data = json.load(f)
    except subprocess.CalledProcessError as e:
        if e.returncode == 22:
            print_error(f"{model}:{model_tag} not found")

        sys.exit(e.returncode)

    pull_ollama_config_blob(repos_ollama, accept,
                            registry_head, manifest_data)
    for layer in manifest_data["layers"]:
        layer_digest = layer["digest"]
        if layer["mediaType"] != 'application/vnd.ollama.image.model':
            continue

        pull_ollama_blob(repos_ollama, layer_digest, accept,
                         registry_head, ramalama_models, model_name, model_tag,
                         symlink_path)

    return symlink_path


def huggingface_download(ramalama_store, model, directory, filename):
    return run_cmd(["huggingface-cli", "download", directory, filename, "--cache-dir", ramalama_store + "/repos/huggingface/.cache", "--local-dir", ramalama_store + "/repos/huggingface/" + directory])


def try_huggingface_download(ramalama_store, model, directory, filename):
    proc = huggingface_download(ramalama_store, model, directory, filename)
    return proc.stdout.decode('utf-8')


def mkdirs(ramalama_store):
    # List of directories to create
    directories = [
        'models/huggingface',
        'repos/huggingface',
        'models/oci',
        'repos/oci',
        'models/ollama',
        'repos/ollama'
    ]

    # Create each directory
    for directory in directories:
        full_path = os.path.join(ramalama_store, directory)
        os.makedirs(full_path, exist_ok=True)


def human_duration(d):
    if d < 1:
        return "Less than a second"
    elif d == 1:
        return "1 second"
    elif d < 60:
        return f"{d} seconds"
    elif d < 120:
        return "1 minute"
    elif d < 3600:
        return f"{d // 60} minutes"
    elif d < 7200:
        return "1 hour"
    elif d < 86400:
        return f"{d // 3600} hours"
    elif d < 172800:
        return "1 day"
    elif d < 604800:
        return f"{d // 86400} days"
    elif d < 1209600:
        return "1 week"
    elif d < 2419200:
        return f"{d // 604800} weeks"
    elif d < 4838400:
        return "1 month"
    elif d < 31536000:
        return f"{d // 2419200} months"
    elif d < 63072000:
        return "1 year"
    else:
        return f"{d // 31536000} years"


def list_files_by_modification():
    return sorted(Path().rglob('*'), key=lambda p: os.path.getmtime(p),
                  reverse=True)


def list_cli(ramalama_store, args, port):
    if len(args) > 0:
        usage()
    print(f"{'NAME':<67} {'MODIFIED':<15} {'SIZE':<6}")
    mycwd = os.getcwd()
    os.chdir(f"{ramalama_store}/models/")
    for path in list_files_by_modification():
        if path.is_symlink():
            name = str(path).replace('/', '://', 1)
            file_epoch = path.lstat().st_mtime
            diff = int(time.time() - file_epoch)
            modified = human_duration(diff) + " ago"
            size = subprocess.run(["du", "-h", str(path.resolve())],
                                  capture_output=True, text=True).stdout.split()[0]
            print(f"{name:<67} {modified:<15} {size:<6}")
    os.chdir(mycwd)


funcDict["list"] = list_cli
funcDict["ls"] = list_cli


def pull_huggingface(model, ramalama_store):
    model = re.sub(r'^huggingface://', '', model)
    directory, filename = model.rsplit('/', 1)
    gguf_path = try_huggingface_download(
        ramalama_store, model, directory, filename)
    directory = f"{ramalama_store}/models/huggingface/{directory}"
    os.makedirs(directory, exist_ok=True)
    symlink_path = f"{directory}/{filename}"
    relative_target_path = os.path.relpath(
        gguf_path.rstrip(), start=os.path.dirname(symlink_path))
    if os.path.exists(symlink_path) and os.readlink(symlink_path) == relative_target_path:
        # Symlink is already correct, no need to update it
        return symlink_path

    try:
        run_cmd(["ln", "-sf", relative_target_path, symlink_path])
    except subprocess.CalledProcessError as e:
        print_error(e)
        sys.exit(e.returncode)

    return symlink_path


def pull_oci(model, ramalama_store):
    target, registry, reference, reference_dir = oci_target_decompose(model)
    outdir = f"{ramalama_store}/repos/oci/{registry}/{reference_dir}"
    print(f"Downloading {target}...")
    # note: in the current way ramalama is designed, cannot do Helper(OMLMDRegistry()).pull(target, outdir) since cannot use modules/sdk, can use only cli bindings from pip installs
    run_cmd(["omlmd", "pull", target, "--output", outdir])
    ggufs = [file for file in os.listdir(outdir) if file.endswith('.gguf')]
    if len(ggufs) != 1:
        print(f"Error: Unable to identify .gguf file in: {outdir}")
        sys.exit(-1)

    directory = f"{ramalama_store}/models/oci/{registry}/{reference_dir}"
    os.makedirs(directory, exist_ok=True)
    symlink_path = f"{directory}/{ggufs[0]}"
    relative_target_path = os.path.relpath(
        f"{outdir}/{ggufs[0]}",
        start=os.path.dirname(symlink_path)
    )
    if os.path.exists(symlink_path) and os.readlink(symlink_path) == relative_target_path:
        # Symlink is already correct, no need to update it
        return symlink_path

    try:
        run_cmd(["ln", "-sf", relative_target_path, symlink_path])
    except subprocess.CalledProcessError as e:
        print_error(e)
        sys.exit(e.returncode)

    return symlink_path


def pull_cli(ramalama_store, args, port):
    if len(args) < 1:
        usage()

    model = args.pop(0)
    matching_files = glob.glob(f"{ramalama_store}/models/*/{model}")
    if matching_files:
        return matching_files[0]

    if model.startswith("huggingface://"):
        return pull_huggingface(model, ramalama_store)
    if model.startswith("oci://"):
        return pull_oci(model, ramalama_store)

    model = re.sub(r'^ollama://', '', model)
    repos_ollama = ramalama_store + "/repos/ollama"
    ramalama_models = ramalama_store + "/models/ollama"
    registry = "https://registry.ollama.ai"
    if '/' in model:
        model_full = model
    else:
        model_full = "library/" + model

    accept = "Accept: application/vnd.docker.distribution.manifest.v2+json"
    if ':' in model_full:
        model_name, model_tag = model_full.split(':', 1)
    else:
        model_name = model_full
        model_tag = "latest"

    model_base = os.path.basename(model_name)
    symlink_path = os.path.join(ramalama_models, f"{model_base}:{model_tag}")
    if os.path.exists(symlink_path):
        return symlink_path

    manifests = os.path.join(repos_ollama, "manifests",
                             registry, model_name, model_tag)
    registry_head = f"{registry}/v2/{model_name}"
    return init_pull(repos_ollama, manifests, accept, registry_head, model_name, model_tag, ramalama_models, symlink_path, model)


funcDict["pull"] = pull_cli


def oci_target_decompose(model):
    # Remove the prefix and extract target details
    target = re.sub(r'^oci://', '', model)
    registry, reference = target.split('/', 1)
    if "." not in registry:
        print_error(f"You must specify a registry for the model in the form 'oci://registry.acme.org/ns/repo:tag', got instead: {model}")
        sys.exit(1)
    reference_dir = reference.replace(":", "/")
    return target, registry, reference, reference_dir


def push_oci(ramalama_store, model, target):
    _, registry, _, reference_dir = oci_target_decompose(model)
    target = re.sub(r'^oci://', '', target)
    
    # Validate the model exists locally
    local_model_path = os.path.join(
        ramalama_store, 'models/oci', registry, reference_dir)
    if not os.path.exists(local_model_path):
        print_error(f"Model {model} not found locally. Cannot push.")
        sys.exit(1)

    model_file = Path(local_model_path).resolve()
    try:
        # Push the model using omlmd, using cwd the model's file parent directory
        run_cmd(["omlmd", "push", target, str(model_file), "--empty-metadata"], cwd=model_file.parent)
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to push model to OCI: {e}")
        sys.exit(e.returncode)

    return local_model_path


def push_cli(ramalama_store, args, port):
    if len(args) < 2:
        usage()

    model = args.pop(0)
    target = args.pop(0)
    if model.startswith("oci://"):
        return push_oci(ramalama_store, model, target)

    # TODO: Additional repository types can be added here, e.g., Ollama, HuggingFace, etc.
    else:
        print_error(f"Unsupported repository type for model: {model}")
        sys.exit(1)


funcDict["push"] = push_cli


def run_cli(ramalama_store, args, port):
    if len(args) < 1:
        usage()

    symlink_path = pull_cli(ramalama_store, args, port)
    exec_cmd(["llama-cli", "-m",
              symlink_path, "--log-disable", "-cnv", "-p", "You are a helpful assistant"])


funcDict["run"] = run_cli


def serve_cli(ramalama_store, args, port):
    if len(args) < 1:
        usage()

    symlink_path = pull_cli(ramalama_store, args, port)
    exec_cmd(["llama-server", "--port", port, "-m", symlink_path])


funcDict["serve"] = serve_cli


def usage():
    print("Usage:")
    print(f"  {os.path.basename(__file__)} COMMAND")
    print()
    print("Commands:")
    print("  list              List models")
    print("  pull MODEL        Pull a model")
    print("  push MODEL TARGET Push a model to target")
    print("  run MODEL         Run a model")
    print("  serve MODEL       Serve a model")
    sys.exit(1)


def get_ramalama_store():
    if os.geteuid() == 0:
        return "/var/lib/ramalama"

    return os.path.expanduser("~/.local/share/ramalama")


def in_container():
    if os.path.exists("/run/.containerenv") or os.path.exists("/.dockerenv") or os.getenv("container"):
        return True

    return False


def available(cmd):
    return shutil.which(cmd) is not None


def select_container_manager():
    if sys.platform == "darwin":
        return ""

    if available("podman"):
        return "podman"

    if available("docker"):
        return "docker"

    return ""


def main(args):
    conman = select_container_manager()
    ramalama_store = get_ramalama_store()
    mkdirs(ramalama_store)

    try:
        dryrun = False
        while len(args) > 0:
            if args[0] == "--dryrun":
                args.pop(0)
                dryrun = True
            elif args[0] in funcDict:
                break
            else:
                print(f"Error: unrecognized command `{args[0]}`\n")
                usage()

        port = "8080"
        host = os.getenv('RAMALAMA_HOST', port)
        if host != port:
            port = host.rsplit(':', 1)[1]

        if conman:
            home = os.path.expanduser('~')
            conman_args = [conman, "run", "--rm", "-it", "--security-opt=label=disable", f"-v{ramalama_store}:/var/lib/ramalama", f"-v{home}:{home}", "-v/tmp:/tmp",
                           f"-v{__file__}:{__file__}", "-e", "RAMALAMA_HOST", "-p", f"{host}:{port}", "quay.io/ramalama/ramalama:latest", __file__] + args
            if dryrun:
                return print(*conman_args)

            exec_cmd(conman_args)

        cmd = args.pop(0)
        funcDict[cmd](ramalama_store, args, port)
    except IndexError:
        usage()
    except KeyError:
        print(cmd + " not valid\n")
        usage()


if __name__ == "__main__":
    main(sys.argv[1:])
