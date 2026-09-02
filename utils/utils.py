import os
import subprocess


def _mount_list(mounts):
    return [mounts] if isinstance(mounts, str) else list(mounts)


def create_folder_structure(base_path):
    # Define the folder structure
    folder_structure = [
        'process',
        'process/data',
        'process/results',
        'process/temp',
        'process/temp/_mask'
    ]

    # Create each folder if it does not exist
    for folder in folder_structure:
        path = os.path.join(base_path, folder)
        if not os.path.exists(path):
            os.makedirs(path)
            print(f"Created folder: {path}")
        else:
            print(f"Folder already exists: {path}")

def execute_cmd(
    params_path,
    hold,
    local_dir,
    force_dir,
    force_image="davidfrantz/force:3.9.02",
    use_sudo=True,
):
    cmd = []
    if use_sudo:
        cmd.append("sudo")
    cmd.extend(["docker", "run", "--rm"])
    for mount in _mount_list(local_dir):
        cmd.extend(["-v", mount])
    if force_dir:
        cmd.extend(["-v", force_dir])
    cmd.extend(
        [
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            force_image,
            "force-higher-level",
            params_path,
        ]
    )
    print("Running command:")
    print(" ".join(cmd))
    if hold:
        subprocess.run(["xterm", "-hold", "-e", *cmd], check=True)
    else:
        subprocess.run(cmd, check=True)
