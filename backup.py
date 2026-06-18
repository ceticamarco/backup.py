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
import signal
import struct
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar, Optional, List, TypeAlias

HEADER_SIZE = 16
HEADER_FORMAT = ">QQ"
HEADER_MAGIC_NUMBER = 0x424B5F50595F4844 # BK_PY_HD
WORKDIR_NAME = "backup.py.tmp"
TARBALL_NAME = "backup.py.tar.gz"

T = TypeVar("T")
@dataclass(frozen=True)
class Ok(Generic[T]):
    """Sum type to represent results"""
    value: T

@dataclass(frozen=True)
class Err:
    error: str

Result: TypeAlias = Ok[T] | Err

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

class SignalHandler:
    """Gracefully handle SIGINT (C-c)"""
    def __init__(self) -> None:
        self.interrupted = False
        self.output_path: Optional[Path] = None
        self.checksum_file: Optional[Path] = None

    def setup(self, output_path: Path, checksum_file: Optional[Path] = None) -> None:
        """Configure signal handler with cleanup paths"""
        self.output_path = output_path
        self.checksum_file = checksum_file
        signal.signal(signal.SIGINT, self.handle_interrupt)

    def handle_interrupt(self, _sig_num: int, _frame: Any) -> None:
        """Handle SIGINT signal"""
        # Second C-c: just exit without cleanup
        if self.interrupted:
            print("\nForced exit. temporary files NOT cleaned.", file=sys.stderr)
            sys.exit(130) # that is, 128 + SIGINT(2)

        # First C-c: cleanup and set flag
        self.interrupted = True
        print(
            "\nBackup interrupted.\nCleaning up temporary files (press CTRL+C again to force exit)...",
            file=sys.stderr,
            end='',
            flush=True
        )

        if self.output_path:
            temp_files = [
                self.output_path / WORKDIR_NAME,
                self.output_path / TARBALL_NAME
            ]

            if self.checksum_file:
                temp_files.append(self.checksum_file)

            Backup.cleanup_files(*temp_files)

        print("DONE", file=sys.stderr)
        sys.exit(130)

class EscapeChar(Enum):
    """Enumeration for escape characters"""
    RESET = '\033[0m'
    GRAY = '\033[90m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    LINE_UP = '\033[A'
    ERASE_LINE = '\033[2K'

class BackupProgress:
    """Progress indicator for backup operations"""
    def __init__(self, total: int, operation: str, status_msg: str) -> None:
        self.total = total
        self.current = 0
        self.operation = operation
        self.status_msg = status_msg
        self.start_time = 0

    def start_time_tracking(self, existing_time: float | None = None) -> None:
        """Initialize time tracking"""
        self.start_time = time.time() if not existing_time else existing_time

    def log_operation(self) -> None:
        """Print the Backup operation to stdout"""
        self.start_time_tracking()
        print(self.operation)

    def draw_progress_bar(self, filename: str = "") -> None:
        """draw progress bar"""
        self.current += 1

        actual = min(self.current, self.total)
        percentage = (actual / self.total) * 100 if self.total > 0 else 0

        # Create a CLI progress bar
        bar_width = 30
        filled = int(bar_width * actual / self.total)
        bar = f"{EscapeChar.GRAY.value}{'█' * filled}{'░' * (bar_width - filled)}{EscapeChar.RESET.value}"

        # Truncate filename if it's too long to display
        # by keeping the first 30 characters + extension (if available)
        # This prevents UI disruption
        filename_max_len = 35
        ext_max_len = 10
        if len(filename) > filename_max_len:
            ext_idx = filename.rfind('.')
            if ext_idx > 0 and len(filename) - ext_idx <= ext_max_len:
                ext = filename[ext_idx:]
                filename = filename[:filename_max_len - len(ext) - 5] + "..." + ext
            else:
                filename = filename[:filename_max_len - 5]

        progress_bar = (f"\r {self.status_msg} [{bar}] "
                        f"{EscapeChar.YELLOW.value}{percentage:.1f}%{EscapeChar.RESET.value} "
                        f"({actual}/{self.total}): "
                        f"{EscapeChar.BLUE.value}'{filename}'{EscapeChar.RESET.value}")
        print(f"{EscapeChar.ERASE_LINE.value}{progress_bar}", end='', flush=True)

    def complete_task(self) -> None:
        """Complete a task"""
        # To complete a task, we do the following:
        #  1. Move the cursor one line upwards
        #  2. Move the cursor at end of operation message (i.e., rewrite the message)
        #  3. Add duration there
        #  4. Move the cursor downwards one line
        duration = Backup.prettify_timestamp(time.time() - self.start_time)
        print(f"{EscapeChar.LINE_UP.value}\r{self.operation}{EscapeChar.GREEN.value}DONE{EscapeChar.RESET.value} "
              f"({EscapeChar.CYAN.value}{duration}{EscapeChar.RESET.value})\n")

class Backup:
    @staticmethod
    def build_header(entry_count: int) -> bytes:
        """Build an header containing backup metadata"""
        # Header format:
        #   big endian (>), 1 uint64 (Q, 8 bytes) + 1 uint64 (Q, 8 bytes) = 16 bytes

        return struct.pack(
            HEADER_FORMAT,
            HEADER_MAGIC_NUMBER,
            entry_count
        )

    @staticmethod
    def parse_header(data: bytes) -> Result[int]:
        """Parse metadata from a backup file"""
        if len(data) < HEADER_SIZE:
            return Err("File too small to contain a valid header.")

        try:
            magic, entry_count = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        except struct.error as err:
            return Err(f"Malformed header: {err}.")

        if magic != HEADER_MAGIC_NUMBER:
            return Err("Invalid magic number.")

        return Ok(entry_count)

    @staticmethod
    def check_deps() -> Result[None]:
        """Check whether dependencies are installed"""
        missing_deps: list[str] = []
        for dep in ["gpg", "tar"]:
            if not shutil.which(dep):
                missing_deps.append(dep)

        if missing_deps:
            return Err(f"Missing dependencies: {', '.join(missing_deps)}.")

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
    def prettify_timestamp(timestamp: float) -> str:
        """Convert a timestamp in seconds to human-readable format"""
        timestamp_int = int(timestamp)

        hours = timestamp_int // 3600
        minutes = (timestamp_int % 3600) // 60
        seconds = timestamp_int % 60

        parts: list[str] = []
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if seconds > 0 or not parts: # show seconds if other parts are zero
            parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

        return ", ".join(parts)

    @staticmethod
    def parse_sources_file(sources_file: Path) -> Result[List[BackupSource]]:
        """Parse the sources file returning a list of BackupSource elements"""
        if not sources_file.exists():
            return Err("Sources file does not exist.")

        sources: List[BackupSource] = []
        try:
            with open(sources_file, 'r') as f:
                for pos, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    if '=' not in line:
                        return Err(f"invalid format at line {pos}: '{line}'.")

                    label, path_str = line.split('=', 1)
                    path = Path(path_str.strip())

                    if not path.exists():
                        return Err(f"Path does not exist: '{path}'.")

                    sources.append(BackupSource(label.strip(), path))
        except IOError as err:
            return Err(f"Failed to read sources file: '{err}'.")

        if not sources:
            return Err("No valid sources found in file.")

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

            return Err(f"The following source element is neither a file nor a directory: '{source}'.")

        except (IOError, OSError, shutil.Error) as err:
            return Err(f"Copy failed: {err}.")

    @staticmethod
    def cleanup_files(*paths: Path | None) -> None:
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
        files: list[Path] = []
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
            return Err(f"Failed to read file '{file_path}': {e}.")

    @staticmethod
    def count_tar_entries(source_dir: Path) -> int:
        """Count all entries (files, dirs) processed by tar including the root directory"""
        return sum(1 for _ in source_dir.rglob('*')) + 1

    @staticmethod
    def create_tarball(source_dir: Path, output_file: Path, verbose: bool) -> Result[int]:
        """Create a compressed tar archive of the backup directory"""
        total_entries = Backup.count_tar_entries(source_dir)
        progress: BackupProgress | None = None
        if verbose:
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
                return Err("Failed to capture output.")

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
            return Err("Cannot create compressed archive.")

        return Ok(total_entries)

    @staticmethod
    def encrypt_file(input_file: Path, output_file: Path, password: str,
                     entry_count: int, verbose: bool) -> Result[None]:
        """Encrypt a file with GPG and prepend an header"""
        start_time = time.time()

        if output_file.exists():
            return Err("Encryption failed: archive already exists.")

        if verbose:
            print("Encrypting backup...", end='', flush=True)
        
        # Prepend the header to the tarball first and then encrypt it
        tmp_payload = input_file.with_suffix(".payload.tmp")
        try:
            header = Backup.build_header(entry_count)
            with open(tmp_payload, "wb") as out, open(input_file, "rb") as src:
                out.write(header)
                shutil.copyfileobj(src, out)
        except IOError as err:
            return Err(f"Failed to prepend header to tarball: {err}.")

        cmd = [
            "gpg", "-a",
            "--symmetric",
            "--cipher-algo=AES256",
            "--no-symkey-cache",
            "--pinentry-mode=loopback",
            "--batch",
            "--passphrase-fd", "0",
            "--output", str(output_file),
            str(tmp_payload)
        ]

        result = subprocess.run(
            cmd,
            input=password.encode(),
            capture_output=not verbose
        )

        tmp_payload.unlink(missing_ok=True)

        if result.returncode != 0:
            return Err(f"Encryption failed: {result.stderr.decode()}.")

        if verbose:
            duration = Backup.prettify_timestamp(time.time() - start_time)
            print(f"{EscapeChar.GREEN.value}DONE{EscapeChar.RESET.value}"
                  f" ({EscapeChar.CYAN.value}{duration}{EscapeChar.RESET.value})")
            
        return Ok(None)

    def make_backup(self, config: BackupState) -> Result[None]:
        """Create an encrypted backup from specified sources file"""
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
        temp_tarball = config.output_path / TARBALL_NAME

        # Backup each source
        sources_count = len(config.sources)
        for idx, source in enumerate(config.sources, 1):
            if config.verbose:
                start_time = time.time()
                print(f"Copying {source.label} ({idx}/{sources_count})...", end='', flush=True)

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
                case Ok():
                    if config.verbose:
                        duration = Backup.prettify_timestamp(time.time() - start_time)
                        print(f"{EscapeChar.GREEN.value}DONE{EscapeChar.RESET.value}"
                              f" ({EscapeChar.CYAN.value}{duration}{EscapeChar.RESET.value})")

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
        entry_count: int = 0
        archive_res = self.create_tarball(work_dir, temp_tarball, config.verbose)
        match archive_res:
            case Err():
                self.cleanup_files(work_dir, temp_tarball)
                return archive_res
            case Ok(value=entry_count):
                pass

        # Encrypt the archive
        encrypt_res = self.encrypt_file(temp_tarball, backup_archive, config.password, entry_count, config.verbose)
        match encrypt_res:
            case Err():
                self.cleanup_files(work_dir, temp_tarball)
                return encrypt_res
            case Ok():
                pass

        # Cleanup temporary files
        self.cleanup_files(work_dir, temp_tarball)

        # Compute file size
        if not backup_archive.exists():
            return Err("Unable to create backup archive.")

        elapsed_time = time.time() - start_time
        file_size = backup_archive.stat().st_size
        file_size_hr = self.prettify_size(file_size)

        # Print a table containing some information about the backup
        if config.verbose:
            rows = [
                ("File name", f"'{backup_archive}'"),
                ("File size", f"{file_size} bytes ({file_size_hr})"),
                ("Elapsed time", f"{self.prettify_timestamp(elapsed_time)}")
            ]

            if config.checksum:
                rows.insert(1, ("Checksum file", f"'{checksum_file}'"))

            # Compute column widths
            max_label_width = max(len(label) for label, _ in rows)
            max_value_width = max(len(value) for _, value in rows)

            separator = f"+{'-' * (max_label_width + 2)}+{'-' * (max_value_width + 2)}+"
            print(separator)
            for label, value in rows:
                print(f"| {label:<{max_label_width}} | {value:<{max_value_width}} |")
                print(separator)

        return Ok(None)

    @staticmethod
    def decrypt_file(input_file: Path, output_file: Path, password: str, verbose: bool) -> Result[int]:
        """Strip header, decrypt the backup file and return entry count"""
        start_time = 0
        if verbose:
            start_time = time.time()
            print("Decrypting backup...", end='', flush=True)

        tmp_decrypted = input_file.with_suffix(".dec.tmp")

        cmd = [
            "gpg", "-a",
            "--quiet",
            "--decrypt",
            "--no-symkey-cache",
            "--pinentry-mode=loopback",
            "--batch",
            "--passphrase-fd", "0",
            "--output", str(tmp_decrypted),
            str(input_file)
        ]

        result = subprocess.run(
            cmd,
            input=password.encode(),
            capture_output=True
        )

        if result.returncode != 0:
            tmp_decrypted.unlink(missing_ok=True)
            return Err(f"Decryption failed: {result.stderr.decode()}.")
        
        try:
            with open(tmp_decrypted, "rb") as file:
                # Read header and move file cursor at the end of the header
                header_data = file.read(HEADER_SIZE)

                header_res = Backup.parse_header(header_data)
                match header_res:
                    case Err():
                        return header_res
                    case Ok(value=entry_count):
                        pass

                with open(output_file, "wb") as out:
                    # Write everything after the header, that is the actual file content.
                    # The file cursor was moved at the end of the header by previous
                    # read operation
                    shutil.copyfileobj(file, out)
        except IOError as err:
            return Err(f"Failed to strip header from decrypted file: {err}.")
        finally:
            tmp_decrypted.unlink(missing_ok=True)

        if verbose:
            duration = Backup.prettify_timestamp(time.time() - start_time)
            print(f"{EscapeChar.GREEN.value}DONE{EscapeChar.RESET.value}"
                  f" ({EscapeChar.CYAN.value}{duration}{EscapeChar.RESET.value})")

        return Ok(entry_count)

    @staticmethod
    def extract_tarball(archive_file: Path, entry_count: int, verbose: bool) -> Result[Path]:
        """Extract a tar archive and return the extracted path"""
        start_time = 0
        if verbose:
            start_time = time.time()
            print("Extracting backup...")

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
            progress = BackupProgress(entry_count, "Extracting backup...", "extracting")
            progress.start_time_tracking(start_time)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        if verbose and progress is not None:
            if process.stdout is None:
                return Err("Failed to capture output.")

            for line in process.stdout:
                line = line.strip()
                if line:
                    filename = Path(line).name
                    progress.draw_progress_bar(filename)
            progress.complete_task()

        # Wait for process to complete
        process.wait()

        if process.returncode != 0:
            return Err("Unable to extract compressed archive.")

        root_path = archive_file.parent / WORKDIR_NAME

        if not root_path.exists():
            return Err(f"Extracted '{root_path}' not found.")

        return Ok(root_path)

    @staticmethod
    def verify_backup(extracted_dir: Path, checksum_file: Path, verbose: bool) -> Result[None]:
        """Verify the integrity of a backup archive"""
        try:
            with open(checksum_file, 'r') as cf:
                expected_hashes = set(line.strip() for line in cf if line.strip())
        except IOError as err:
            return Err(f"Failed to load checksums file: {err}.")

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
        start_time = time.time()
        
        temp_tarball = archive_file.parent / TARBALL_NAME
        entry_count = 0
        decrypt_res = self.decrypt_file(archive_file, temp_tarball, password, verbose)
        match decrypt_res:
            case Err():
                self.cleanup_files(temp_tarball)
                return decrypt_res
            case Ok(value=count):
                entry_count = count

        extracted_dir: Path | None = None
        extract_res = self.extract_tarball(temp_tarball, entry_count, verbose)
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
                case Ok():
                    pass

        self.cleanup_files(temp_tarball)

        elapsed_time = time.time() - start_time

        if verbose:
            print(f"Backup extracted to: '{extracted_dir.parent.resolve() / extracted_dir}'")
            print(f"Elapsed time: {self.prettify_timestamp(elapsed_time)}")

        return Ok(None)

def main():
    signal_handler = SignalHandler()
    parser = argparse.ArgumentParser(
        description="backup.py - modular and lightweight backup utility"
    )

    # Parent parser for commands shared across subcommands
    parent_command = argparse.ArgumentParser(add_help=False)
    parent_command.add_argument(
        "-V", "--verbose",
        action="store_true",
        help="Enable verbose mode"
    )

    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
        help="Available commands"
    )

    # Subparser for the "create" command
    create_cmd = subcommands.add_parser(
        "create",
        parents=[parent_command],
        help="Create a new backup"
    )
    create_cmd.add_argument("sources", help="Path to the sources file")
    create_cmd.add_argument("dest", help="Destination directory")
    create_cmd.add_argument("password", help="Encryption password")
    create_cmd.add_argument(
        "-c", "--checksum",
        action="store_true",
        help="Generate SHA256 checksums"
    )

    # Subparser for the "extract" command
    extract_cmd = subcommands.add_parser(
        "extract",
        parents=[parent_command],
        help="Extract an existing backup archive"
    )
    extract_cmd.add_argument("archive", help="Path to the encrypted archive")
    extract_cmd.add_argument("password", help="Decryption password")
    extract_cmd.add_argument(
        "-c", "--checksum",
        metavar="SHA256_FILE",
        help="Check SHA256 checksums against the provided backup"
    )

    args = parser.parse_args()

    # Check whether dependencies are installed
    deps_res = Backup.check_deps()
    match deps_res:
        case Err(error=e):
            print(f"{e}", file=sys.stderr)
            sys.exit(1)
        case Ok():
            pass

    backup = Backup()

    if args.command == "create":
        # Check root permissions
        if os.geteuid() != 0:
            print("The 'create' option requires root permissions.", file=sys.stderr)
            sys.exit(1)

        sources_path = Path(args.sources)
        output_dir = Path(args.dest)

        # Determine checksum file if requested
        date_str = datetime.now().strftime("%Y%m%d")
        hostname = os.uname().nodename
        checksum_file = output_dir / f"backup-{hostname}-{date_str}.sha256" if args.checksum else None

        signal_handler.setup(output_dir, checksum_file)

        # Check whether output directory exists
        if not output_dir.exists():
            print(f"Output directory '{output_dir}' does not exist.", file=sys.stderr)
            sys.exit(1)

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
                    password=args.password,
                    checksum=args.checksum,
                    verbose=args.verbose
                )

        backup_res = backup.make_backup(config)
        match backup_res:
            case Err(error=e):
                print(f"{e}", file=sys.stderr)
                sys.exit(1)
            case Ok():
                pass

    elif args.command == "extract":
        archive_file = Path(args.archive)
        signal_handler.setup(archive_file.parent)

        if not archive_file.exists():
            print(f"Archive file '{archive_file}' does not exist.", file=sys.stderr)
            sys.exit(1)

        checksum_file: Path | None = None

        if args.checksum:
            checksum_file = Path(args.checksum)

            if not checksum_file.exists():
                print(f"Checksums file '{checksum_file}' does not exist.", file=sys.stderr)
                sys.exit(1)

        extract_res = backup.extract_backup(archive_file, args.password, checksum_file, args.verbose)
        match extract_res:
            case Err(error=e):
                print(f"{e}", file=sys.stderr)
                sys.exit(1)
            case Ok():
                pass

if __name__ == "__main__":
    main()
