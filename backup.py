#!/usr/bin/env python3
# backup.py: modular and lightweight backup utility
# Developed by Marco Cetica (c) 2018-2026
#

import argparse
import shutil
import sys
import os
import time
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Generic, TypeVar, Union, Optional, List

T = TypeVar("T")
@dataclass(frozen=True)
class Ok(Generic[T]):
    """Sum type to represent results"""
    value: T

@dataclass(frozen=True)
class Err:
    error: str

Result = Union[Ok[T], Err]

@dataclass
class BackupSource:
    """Struct to represent a mapping between a label and a path"""
    label: str
    path: Path

@dataclass
class BackupState:
    """Struct to represent a backup state"""
    sources: List[BackupSource]
    output_path: Path
    password: str
    checksum: bool
    verbose: bool

class BackupProgress:
    """Progress indicator for backup operations"""
    def __init__(self, total: int, operation: str, status_msg: str) -> None:
        self.total = total
        self.current = 0
        self.operation = operation
        self.status_msg = status_msg

    def log_operation(self) -> None:
        """Print the Backup operation to stdout"""
        print(self.operation)

    def draw_progress_bar(self, message: str = "") -> None:
        """draw progress bar"""
        self.current += 1
        percentage = (self.current / self.total) * 100 if self.total > 0 else 0

        # Create a CLI prograss bar
        bar_width = 30
        filled = int(bar_width * self.current / self.total)
        bar = '█' * filled + '░' * (bar_width - filled)

        status = f"\r└──{self.operation} [{bar}] {percentage:.1f}% ({self.current}/{self.total}) - (processing '{message}')"
        print(f"\r\033[K{status}", end='', flush=True)

    def complete_task(self) -> None:
        """Complete a task"""
        # To complete a task, we do the following:
        #  1. Move the cursor one line upwards
        #  2. Move the cursor at end of operation message (i.e., rewrite the message)
        #  3. Add 'DONE' there
        #  4. Move the cursor downwards one line
        print(f'\033[A\r{self.operation}DONE\n')

class Backup:
    @staticmethod
    def check_deps() -> Result[None]:
        """Check whether dependencies are installed"""
        missing_deps = []
        for dep in ["gpg", "tar"]:
            if not shutil.which(dep):
                missing_deps.append(dep)

        if missing_deps:
            return Err(f"Missing dependencies: {', '.join(missing_deps)}")

        return Ok(None)

    @staticmethod
    def prettify_size(byte_size: int) -> str:
        """Convert byte_size in powers of 1024"""
        units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"]
        idx = 0
        size = float(byte_size)

        while size >= 1024.0 and idx < (len(units) - 1):
            size /= 1024.0
            idx += 1

        if size.is_integer():
            return f"{int(size)} {units[idx]}"

        return f"{size:.2f} {units[idx]}"

    @staticmethod
    def parse_sources_file(sources_file: Path) -> Result[List[BackupSource]]:
        """Parse the sources file returning a list of BackupSource elements"""
        if not sources_file.exists():
            return Err("Sources file does not exist")

        sources: List[BackupSource] = []
        try:
            with open(sources_file, 'r') as f:
                for pos, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    if '=' not in line:
                        return Err(f"invalid format at line {pos}: '{line}'")

                    label, path_str = line.split('=', 1)
                    path = Path(path_str.strip())

                    if not path.exists():
                        return Err(f"Path does not exist: {path}")

                    sources.append(BackupSource(label.strip(), path))
        except IOError as err:
            return Err(f"Failed to read sources file: {err}")

        if not sources:
            return Err(f"No valid sources found in file")

        return Ok(sources)

    @staticmethod
    def should_ignore_file(path: Path) -> bool:
        """Check whether a file should be ignored"""
        try:
            # Skip UNIX sockets
            if path.is_socket():
                return True

            # Skip broken symlinks
            if path.is_symlink() and not path.exists():
                return True

            # Skip named pipes (FIFOs)
            if (path.stat().st_mode & 0o170000) == 0o010000:
                return True

            return False
        except (OSError, IOError):
            # Skip files that can't be checked
            return True

    @staticmethod
    def ignore_special_files(directory: str, contents: List[str]) -> List[str]:
        """Return a list of files to ignore"""
        ignored_files: List[str] = []
        dir_path = Path(directory)

        for item in contents:
            item_path = dir_path / item
            if Backup.should_ignore_file(item_path):
                ignored_files.append(item)

        return ignored_files

    @staticmethod
    def copy_files(source: Path, destination: Path) -> Result[None]:
        """Copy files and directories preserving their metadata"""
        try:
            # Handle single file
            if source.is_file():
                # Parent directory might not exists, so we try to create it first
                destination.parent.mkdir(parents=True, exist_ok=True)

                # Copy file and its metadata
                shutil.copy2(source, destination)

                return Ok(None)

            # Handle directory
            if source.is_dir():
                # If destination directory exists, we remove it
                # This approach mimics rsync's --delete option
                if destination.exists():
                    shutil.rmtree(destination)

                # Copy directory and its metadata.
                # We also ignore special files and we preserves links instead
                # of following them.
                shutil.copytree(
                    source,
                    destination,
                    symlinks=True, # True = preserve symlinks
                    copy_function=shutil.copy2,
                    ignore=Backup.ignore_special_files,
                    dirs_exist_ok=False
                )

                return Ok(None)

            return Err(f"The following source element is neither a file nor a directory: {source}")

        except (IOError, OSError, shutil.Error) as err:
            return Err(f"Copy failed: {err}")

    @staticmethod
    def cleanup_files(*paths: Path) -> None:
        """Clean up temporary files and directories"""
        for path in paths:
            if path is None or not path.exists():
                continue

            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    @staticmethod
    def collect_files(directory: Path) -> List[Path]:
        """Collect all files in a directory (recursively)"""
        files = []
        for item in directory.rglob('*'):
            if item.is_file() and not item.is_symlink():
                files.append(item)

        return files

    @staticmethod
    def compute_file_hash(file_path: Path) -> Result[str]:
        """Compute SHA256 hash of a given file"""
        try:
            hash_obj = hashlib.sha256()
            with open(file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    hash_obj.update(byte_block)
            return Ok(hash_obj.hexdigest())
        except IOError as e:
            return Err(f"Failed to read file {file_path}: {e}")

    @staticmethod
    def count_tar_entries(source_dir: Path) -> int:
        """Count all entries (files, dirs) that tar processes"""
        return sum(1 for _ in source_dir.rglob('*'))

    @staticmethod
    def create_tarball(source_dir: Path, output_file: Path, verbose: bool) -> Result[None]:
        """Create a compressed tar archive of the backup directory"""
        progress: BackupProgress | None = None
        if verbose:
            total_entries = Backup.count_tar_entries(source_dir)
            progress = BackupProgress(total_entries, "Compressing backup...", "compressing")
            progress.log_operation()

        cmd = [
            "tar",
            "-czf",
            str(output_file),
            "-C",
            str(source_dir.parent),
            source_dir.name
        ]

        if verbose:
            cmd.insert(1, "-v")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # Read subprocess output from pipe in buffered mode
        if verbose and progress is not None:
            if process.stdout is None:
                return Err("Failed to capture output")

            for line in process.stdout:
                line = line.strip()
                if line:
                    # Extract filename from path
                    filename = Path(line).name
                    progress.draw_progress_bar(filename)
            progress.complete_task()

        # Wait for subprocess to complete
        process.wait()

        if process.returncode != 0:
            return Err("Cannot create compressed archive")

        return Ok(None)

    @staticmethod
    def encrypt_file(input_file: Path, output_file: Path, password: str, verbose: bool) -> Result[None]:
        """Encrypt a file with GPG in symmetric mode (using AES256)"""

        if output_file.exists():
            return Err("Encryption failed: archive already exists")
        
        if verbose:
            print("Encrypting backup...", end='', flush=True)

        cmd = [
            "gpg", "-a",
            "--symmetric",
            "--cipher-algo=AES256",
            "--no-symkey-cache",
            "--pinentry-mode=loopback",
            "--batch",
            "--passphrase-fd", "0",
            "--output", str(output_file),
            str(input_file)
        ]

        result = subprocess.run(
            cmd,
            input=password.encode(),
            capture_output=not verbose
        )

        if result.returncode != 0:
            return Err(f"Encryption failed: {result.stderr.decode()}")

        if verbose:
            print("DONE")
            
        return Ok(None)

    def make_backup(self, config: BackupState) -> Result[None]:
        """Create an encrypted backup from specified sources file"""
        # Check root permissions
        if os.geteuid() != 0:
            return Err("Run this program as root!")

        start_time = time.time()
        date_str = datetime.now().strftime("%Y%m%d")
        hostname = os.uname().nodename

        # Create working directory
        work_dir = config.output_path / "backup.py.tmp"
        if not work_dir.exists():
            work_dir.mkdir(parents=True, exist_ok=True)

        # Format output files
        backup_archive = config.output_path / f"backup-{hostname}-{date_str}.tar.gz.enc"
        checksum_file = config.output_path / f"backup-{hostname}-{date_str}.sha256"
        temp_tarball = config.output_path / "backup.py.tar.gz"

        # Backup each source
        sources_count = len(config.sources)
        for idx, source in enumerate(config.sources, 1):
            print(f"Copying {source.label} ({idx}/{sources_count})")

            # Create source subdirectory
            source_dir = work_dir / f"backup-{source.label}-{date_str}"
            if not source_dir.exists():
                source_dir.mkdir(parents=True, exist_ok=True)

            # Copy files
            copy_res = self.copy_files(source.path, source_dir)
            match copy_res:
                case Err():
                    self.cleanup_files(work_dir, temp_tarball)
                    return copy_res
                case Ok(): pass

            # Compute checksum when requested
            if config.checksum:
                files = self.collect_files(source_dir)

                backup_progress: BackupProgress | None = None

                if config.verbose:
                    backup_progress = BackupProgress(len(files), "Computing checksums...", "computing")
                    backup_progress.log_operation()

                with open(checksum_file, 'a') as checksum_fd:
                    for file in files:
                        hash_result = self.compute_file_hash(file)
                        match hash_result:
                            case Err():
                                checksum_fd.close()
                                self.cleanup_files(work_dir, temp_tarball)
                                return hash_result
                            case Ok(value=v):
                                checksum_fd.write(f"{v}\n")

                        if config.verbose and backup_progress is not None:
                            backup_progress.draw_progress_bar(str(file.name))

                if config.verbose and backup_progress is not None:
                    backup_progress.complete_task()

        # Create compressed archive
        archive_res = self.create_tarball(work_dir, temp_tarball, config.verbose)
        match archive_res:
            case Err():
                self.cleanup_files(work_dir, temp_tarball)
                return archive_res
            case Ok(): pass

        # Encrypt the archive
        encrypt_res = self.encrypt_file(temp_tarball, backup_archive, config.password, config.verbose)
        match encrypt_res:
            case Err():
                self.cleanup_files(work_dir, temp_tarball)
                return encrypt_res
            case Ok(): pass

        # Cleanup temporary files
        self.cleanup_files(work_dir, temp_tarball)

        # Compute file size
        if not backup_archive.exists():
            return Err("Unable to create backup archive")

        elapsed_time = time.time() - start_time
        file_size = backup_archive.stat().st_size
        file_size_hr = Backup.prettify_size(file_size)
        
        print(f"File name: '{backup_archive}'")
        if config.checksum:
            print(f"Checksum file: '{checksum_file}'")
        print(f"File size: {file_size} bytes ({file_size_hr})")
        print(f"Elapsed time: {elapsed_time:.2f} seconds")

        return Ok(None)

    @staticmethod
    def decrypt_file(input_file: Path, output_file: Path, password: str, verbose: bool) -> Result[None]:
        """Decrypt an encrypted backup archive"""
        if verbose:
            print("Decrypting backup...", end='', flush=True)

        cmd = [
            "gpg", "-a",
            "--quiet",
            "--decrypt",
            "--no-symkey-cache",
            "--pinentry-mode=loopback",
            "--batch",
            "--passphrase-fd", "0",
            "--output", str(output_file),
            str(input_file)
        ]

        result = subprocess.run(
            cmd,
            input=password.encode(),
            capture_output=True
        )

        if result.returncode != 0:
            return Err(f"Decryption failed: {result.stderr.decode()}")

        if verbose:
            print("DONE")

        return Ok(None)

    @staticmethod
    def extract_tarball(archive_file: Path, verbose: bool) -> Result[Path]:
        """Extract a tar archive and return the extracted path"""
        if verbose:
            print("Extracting backup...")
        
        extracted_root: str = ""

        # Count archive content
        list_cmd = ["tar", "-tzf", str(archive_file)]
        try:
            list_res = subprocess.run(
                list_cmd,
                capture_output=True,
                text=True,
                check=True
            )
            entries = list_res.stdout.strip().split('\n')
            if not entries or not entries[0]:
                return Err("Archive is empty or corrupted")

            # Retrieve root directory from first entry
            extracted_root = entries[0].split('/')[0]

        except subprocess.CalledProcessError as err:
            return Err(f"Failed to list archive content: {err}")

        cmd = [
            "tar",
            "-xzf",
            str(archive_file),
            "-C",
            str(archive_file.parent)
        ]

        progress: BackupProgress | None = None

        if verbose:
            cmd.insert(1, "-v")
            progress = BackupProgress(len(entries), "Extracting backup...", "extracting")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        if verbose and progress is not None:
            if process.stdout is None:
                return Err("Failed to capture output")

            for line in process.stdout:
                line = line.strip()
                if line:
                    filename = Path(line).name
                    progress.draw_progress_bar(filename)
            progress.complete_task()

        # Wait for process to complete
        process.wait()

        if process.returncode != 0:
            return Err("Unable to extract compressed archive")

        root_path = archive_file.parent / extracted_root

        if not root_path.exists():
            return Err(f"Extracted '{root_path}' not found")

        return Ok(root_path)

    @staticmethod
    def verify_backup(extracted_dir: Path, checksum_file: Path, verbose: bool) -> Result[None]:
        """Verify the integrity of a backup archive"""
        try:
            with open(checksum_file, 'r') as cf:
                expected_hashes = set(line.strip() for line in cf if line.strip())
        except IOError as err:
            return Err(f"Failed to load checksum file: {err}")

        files = Backup.collect_files(extracted_dir)
        progress = None
        
        if verbose:
            progress = BackupProgress(len(files), "Verifying backup...", "verifying")
            progress.log_operation()

        for file in files:
            hash_res = Backup.compute_file_hash(file)
            match hash_res:
                case Err():
                    return hash_res
                case Ok(value=file_hash):
                    if file_hash not in expected_hashes:
                        return Err(f"{'\n' if verbose else ''}!! Integrity error for '{file}' !!")

            if verbose and progress is not None:
                progress.draw_progress_bar(file.name)

        if verbose and progress is not None:
            progress.complete_task()

        return Ok(None)
    
    def extract_backup(self, archive_file: Path, password: str, checksum_file: Optional[Path], verbose: bool) -> Result[None]:
        """Extract and verify a backup archive"""
        temp_tarball = archive_file.parent / Path("backup.py.tar.gz")

        decrypt_res = self.decrypt_file(archive_file, temp_tarball, password, verbose)
        match decrypt_res:
            case Err():
                self.cleanup_files(temp_tarball)
                return decrypt_res
            case Ok(): pass

        extracted_dir: Path | None = None
        extract_res = self.extract_tarball(temp_tarball, verbose)
        match extract_res:
            case Err():
                self.cleanup_files(temp_tarball)
                return extract_res
            case Ok(value=root_dir):
                extracted_dir = root_dir

        # Verify checksums when required
        if checksum_file:
            checksums_res = self.verify_backup(extracted_dir, checksum_file, verbose)
            match checksums_res:
                case Err():
                    self.cleanup_files(temp_tarball, extracted_dir)
                    return checksums_res
                case Ok(): pass

        self.cleanup_files(temp_tarball)
        print(f"Backup extracted to: '{extracted_dir.parent.resolve() / extracted_dir}'")

        return Ok(None)

def main():
    parser = argparse.ArgumentParser(
        description="backup.py - modular and lightweight backup utility"
    )

    parser.add_argument(
        "-b", "--backup",
        nargs=3,
        metavar=("SOURCES", "DEST", "PASS"),
        help="Backup files from SOURCES path to DEST directory with password PASS"
    )

    parser.add_argument(
        "-e", "--extract",
        nargs="+",
        metavar="ARCHIVE",
        help="Extract ARCHIVE (optionally with PASS and SHA256 file)"
    )

    parser.add_argument(
        "-c", "--checksum",
        action="store_true",
        help="Generate or check SHA256 checksums"
    )

    parser.add_argument(
        "-V", "--verbose",
        action="store_true",
        help="Enable verbose mode"
    )

    args = parser.parse_args()

    if not (args.backup or args.extract):
        parser.error("specify either --backup or --extract")

    # Check whether dependencies are installed
    deps_res = Backup.check_deps()
    match deps_res:
        case Err(error=e):
            print(f"{e}", file=sys.stderr)
            sys.exit(1)
        case Ok(): pass

    backup = Backup()

    if args.backup:
        sources_file, output_path, encryption_pass = args.backup

        sources_path = Path(sources_file)
        output_dir = Path(output_path)

        # Create output directory if it doesn't exist
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        # Parse sources file
        sources_res = Backup.parse_sources_file(sources_path)
        config: BackupState
        match sources_res:
            case Err(error=e):
                print(f"{e}", file=sys.stderr)
                sys.exit(1)
            case Ok(value=v):
                # Create a backup state
                config = BackupState(
                    sources=v,
                    output_path=output_dir,
                    password=encryption_pass,
                    checksum=args.checksum,
                    verbose=args.verbose
                )

        backup_res = backup.make_backup(config)
        match backup_res:
            case Err(error=e):
                print(f"{e}", file=sys.stderr)
                sys.exit(1)

    elif args.extract:
        archive_file = Path(args.extract[0])

        if not archive_file.exists():
            print(f"Archive file '{archive_file}' does not exist", file=sys.stderr)
            sys.exit(1)

        decryption_pass: str = ""
        checksum_file: Path | None = None

        if len(args.extract) >= 2:
            decryption_pass = args.extract[1]
        else:
            print("--extract flag requires decryption password as second argument", file=sys.stderr)
            sys.exit(1)

        if args.checksum:
            if len(args.extract) >= 3:
                checksum_file = Path(args.extract[2])
            else:
                print("--checksum flag requires SHA256 file as third argument", file=sys.stderr)
                sys.exit(1)

            if not checksum_file.exists():
                print(f"Checksum file '{checksum_file}' does not exist", file=sys.stderr)
                sys.exit(1)

        extract_res = backup.extract_backup(archive_file, decryption_pass, checksum_file, args.verbose)
        match extract_res:
            case Err(error=e):
                print(f"{e}", file=sys.stderr)
                sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
