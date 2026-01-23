# Test suite for backup.py
# Usage: sudo ./test_backup.py <PATH_TO_BACKUP_PY>
# Developed by Marco Cetica 2026
#

import subprocess
import tempfile
import shutil
import os
import sys
from pathlib import Path
from typing import List, Tuple

class TestBackup:
    """Test suite for backup.py"""
    def __init__(self, backup_script: Path):
        self.backup_script = backup_script
        self.test_dir: Path | None = None
        self.sources_file: Path | None = None
        self.backup_dir: Path | None = None
        self.test_password = "very_bad_pw"
        self.passed = 0
        self.failed = 0

    def setup(self) -> None:
        """Create test environment"""
        print("Setting up test environment...")

        # Create temp directory
        self.test_dir = Path(tempfile.mkdtemp(prefix="backup_test_"))
        self.backup_dir = self.test_dir / "backups"
        self.backup_dir.mkdir()

        # Create test data directories
        test_data = self.test_dir / "test_data"
        test_data.mkdir()

        # Create some test data
        (test_data / "dir1").mkdir()
        (test_data / "dir1" / "file1.txt").write_text("Test content 1\n" * 100)
        (test_data / "dir1" / "file2.txt").write_text("Test content 2\n" * 100)

        (test_data / "dir2").mkdir()
        (test_data / "dir2" / "subdir").mkdir()
        (test_data / "dir2" / "subdir" / "nested.txt").write_text("Nested content\n" * 50)
        (test_data / "dir2" / "data.bin").write_bytes(b"\x00\x01\x02\x03" * 1000)
        (test_data / "single_file.txt").write_text("Single file content\n" * 20)

        # Create sources file
        self.sources_file = self.test_dir / "sources.ini"
        with open(self.sources_file, 'w') as f:
            f.write(f"dir1={test_data / 'dir1'}\n")
            f.write(f"dir2={test_data / 'dir2'}\n")
            f.write(f"single={test_data / 'single_file.txt'}\n")

        print(f"Test directory: {self.test_dir}")
        print(f"Test data created in: {test_data}")

    def cleanup(self) -> None:
        """Remove test environment"""
        if self.test_dir and self.test_dir.exists():
            print(f"\nClearing up test directory: {self.test_dir}")
            shutil.rmtree(self.test_dir)

    def run_backup_command(self, args: List[str], check: bool = True) -> Tuple[int, str, str]:
        """Run backup.py with given arguments"""
        cmd = [sys.executable, str(self.backup_script)] + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        if check and result.returncode != 0:
            print(f"Command failed: {' '.join(cmd)}")
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")

        return result.returncode, result.stdout, result.stderr

    def find_backup_archive(self) -> Path | None:
        """Find the most recent backup archive"""
        if not self.backup_dir:
            return None

        archives = list(self.backup_dir.glob("backup-*.tar.gz.enc"))

        return archives[-1] if archives else None

    def find_checksum_file(self) -> Path | None:
        """Find the most recent checksums file"""
        if not self.backup_dir:
            return None

        checksums = list(self.backup_dir.glob("backup-*.sha256"))

        return checksums[-1] if checksums else None

    def verify_files_exist(self, extracted_dir: Path, expected_labels: List[str]) -> bool:
        """Verify that expected backup directories exist"""
        for label in expected_labels:
            # Find directories matching the pattern 'backup-{label}-*'
            matching = list(extracted_dir.glob(f"backup-{label}-*"))
            if not matching:
                print(f"Missing backup directory for label '{label}'")
                return False

            print(f"Found backup directory: {matching[0].name}")
        return True

    def test_backup_without_checksum(self) -> bool:
        """Test: create backup without checksum"""
        print("\n[TEST 1] Backup creation without checksum")

        returncode, _, _ = self.run_backup_command([
            "--backup",
            str(self.sources_file),
            str(self.backup_dir),
            self.test_password
        ])

        if returncode != 0:
            print("Backup creation failed")
            return False

        # Check whether backup archive was created
        archive = self.find_backup_archive()
        if not archive:
            print("Backup archive not found")
            return False

        print(f"Backup archive created: {archive.name}")

        # Verify checksum file was NOT created
        checksum = self.find_checksum_file()
        if checksum:
            print("Checksums file should not exist")
            return False

        print("Backup creation without checksum was successful")
        return True

    def test_backup_with_checksum(self) -> bool:
        """Test: create backup with checksum"""
        print("\n[TEST 2] Backup creation with checksum")
        
        if not self.backup_dir:
            print("Backup directory does not exist")
            return False

        # Clean up previous backups
        for f in self.backup_dir.glob("backup-*"):
            f.unlink()

        returncode, _, _ = self.run_backup_command([
            "--backup",
            str(self.sources_file),
            str(self.backup_dir),
            self.test_password,
            "--checksum",
            "--verbose"
        ])

        if returncode != 0:
            print("Backup creation failed")
            return False

        # Check whether backup archive was created
        archive = self.find_backup_archive()
        if not archive:
            print("Backup archive not found")
            return False

        print(f"Backup archive created: {archive.name}")

        # Verify checksum file was created
        checksum = self.find_checksum_file()
        if not checksum:
            print("Checksums file not found")
            return False

        # Verify checksum file has content
        if checksum.stat().st_size == 0:
            print("Checksums file is empty")
            return False

        print("Backup creation with checksum was successful")
        return True

    def test_backup_extraction(self) -> bool:
        """Test: Extract backup without verification"""
        print("\n[TEST 3] Backup extraction without verification")

        archive = self.find_backup_archive()
        if not archive:
            print("No backup archive found")
            return False

        returncode, _, _ = self.run_backup_command([
            "--extract",
            str(archive),
            self.test_password,
            "--verbose"
        ])

        if returncode != 0:
            print("Backup extraction failed")
            return False

        print("Backup extracted successfully")

        # Find extracted directory
        if not self.backup_dir:
            print("Backup directory does not exist")
            return False
        
        extracted_dirs = list(self.backup_dir.glob("backup.py.tmp"))
        # Filter out .enc and .sha256 files
        extracted_dirs = [d for d in extracted_dirs if d.is_dir()]

        if not extracted_dirs:
            print("No extracted directory found")
            return False

        extracted_dirs = extracted_dirs[0]
        print(f"Found extracted directory: {extracted_dirs.name}")

        # Verify whether expected backup directories exist
        if not self.verify_files_exist(extracted_dirs, ["dir1", "dir2", "single"]):
            return False

        # Leave test envuronment clean for next test
        shutil.rmtree(extracted_dirs)

        return True

    def test_backup_verification(self) -> bool:
        """Test: Extract and verify backup with checksum"""
        print("\n[TEST 4] Backup extraction with checksum verification")

        archive = self.find_backup_archive()
        checksums = self.find_checksum_file()

        if not archive or not checksums:
            print("Archive or checksums file not found")
            return False

        returncode, _, _ = self.run_backup_command([
            "--checksum",
            "--extract",
            str(archive),
            self.test_password,
            str(checksums),
            "--verbose"
        ])

        if returncode != 0:
            print("Backup verification failed")
            return False

        print("Backup extracted and verifying successfully")

        # Find extracted directory
        if not self.backup_dir:
            print("Backup directory does not exist")
            return False

        extracted_dirs = list(self.backup_dir.glob("backup.py.tmp"))
        extracted_dirs = [d for d in extracted_dirs if d.is_dir()]

        if not extracted_dirs:
            print("No extracted directory found")
            return False

        extracted_dir = extracted_dirs[0]
        print(f"Found extracted directory: {extracted_dir.name}")

        # Verify whether expected backup directories exist
        if not self.verify_files_exist(extracted_dir, ["dir1", "dir2", "single"]):
            return False

        return True

    def test_invalid_sources_syntax(self) -> bool:
        """Test: Invalid syntax in sources file"""
        print("\n[TEST 5] Invalid sources file syntax")

        # Create sources file with invalid syntax (missing '=' token)
        if not self.test_dir:
            print("Testing environment does not exist")
            return False
        
        invalid_sources = self.test_dir / "invalid_sources.ini"
        with open(invalid_sources, 'w') as f:
            f.write(f"valid_entry={self.test_dir}\n")
            f.write("invalid entry\n")
            f.write(f"another_valid={self.test_dir}\n")

        returncode, _, stderr = self.run_backup_command([
            "--backup",
            str(invalid_sources),
            str(self.backup_dir),
            self.test_password,
        ], check=False)

        if returncode == 0:
            print("Should have failed with invalid syntax")
            return False

        # Check whether error message mentions the syntax error
        if "invalid format" in stderr.lower():
            print("Invalid syntax detected correctly")
            print(f"Error message: {stderr.strip()}")
            return True
        else:
            print("Error message doesn't mention syntax error")
            print(f"Received: {stderr.strip()}")
            return False

    def test_missing_source_path(self) -> bool:
        """Test: Source paht doesn't exist"""
        print("\n[TEST 6] Missing source path")

        # Create sources file with invalid path
        if not self.test_dir:
            print("Testing environment does not exist")
            return False
        
        missing_sources = self.test_dir / "missing_sources.ini"
        with open(missing_sources, 'w') as f:
            f.write("existing=/tmp\n")
            f.write("missing=/invalid/path/foo/bar\n")

        returncode, _, stderr = self.run_backup_command([
            "--backup",
            str(missing_sources),
            str(self.backup_dir),
            self.test_password,
        ], check=False)

        if returncode == 0:
            print("Should have failed with missing path")
            return False

        # Check whether error message mentions the missing path
        if "does not exist" in stderr.lower() or "path" in stderr.lower():
            print("Missing path detected correctly")
            print(f"Error message: {stderr.strip()}")
            return True
        else:
            print("Error message doesn't mention missing path")
            print(f"Received: {stderr.strip()}")
            return False

    def test_checksum_corruption_detection(self) -> bool:
        """Test: Verify whether corrupted files are detected"""
        print("\n[TEST 7] Checksum corruption detection")

        archive = self.find_backup_archive()
        checksum = self.find_checksum_file()

        if not archive or not checksum:
            print("Archive or checksum file not found")
            return False

        # Extract without verification
        returncode, _, _ = self.run_backup_command([
            "--extract",
            str(archive),
            self.test_password
        ], check=False)

        if returncode != 0:
            print("Failed to extract archive for corruption test")
            return False

        # Find extracted directory
        if not self.backup_dir:
            print("Backup directory does not exist")
            return False

        extracted_dirs = list(self.backup_dir.glob("backup.py.tmp"))
        extracted_dirs = [d for d in extracted_dirs if d.is_dir()]

        if not extracted_dirs:
            print("No extracted directory found")
            return False

        extracted_dir = extracted_dirs[0]
        
        # Load files
        files = list(extracted_dir.rglob("*.txt"))
        if not files:
            print("No files found to corrupt")
            return False

        corrupt_file = files[0]
        print(f"Corrupting file '{corrupt_file.relative_to(extracted_dir)}")

        # Corrupt the file by appending data
        with open(corrupt_file, 'a') as f:
            f.write("\nCORRUPTED DATA\n")

        # Verify it
        from backup import Backup, Err, Ok
        verify_res = Backup.verify_backup(extracted_dir, checksum, False)
        print(verify_res)

        match verify_res:
            case Err(error=e):
                print(f"Corruption detected correctly: {e}")
                shutil.rmtree(extracted_dir)
                return True
            case Ok():
                print("Corruption was NOT detected")
                shutil.rmtree(extracted_dir)
                return False

    def run_all_tests(self) -> None:
        """Run all tests"""
        print('=' * 60)
        print(' ' * 20 + "BACKUP.PY TEST SUITE")
        print('=' * 60)

        if os.geteuid() != 0:
            print("Run this program as root")
            sys.exit(1)

        if not self.backup_script.exists():
            print(f"backup.py not found at '{self.backup_script}'")
            sys.exit(1)

        try:
            self.setup()

            tests = [
                ("Backup without checksum", self.test_backup_without_checksum),
                ("Backup with checksum", self.test_backup_with_checksum),
                ("Backup extraction", self.test_backup_extraction),
                ("Backup verification", self.test_backup_verification),
                ("Invalid sources syntax", self.test_invalid_sources_syntax),
                ("Missing source path", self.test_missing_source_path),
                ("Corruption detection", self.test_checksum_corruption_detection)
            ]

            for name, test_fun in tests:
                try:
                    if test_fun():
                        self.passed += 1
                        print(f"{name} PASSED")
                    else:
                        self.failed += 1
                        print(f"{name} FAILED")
                except Exception as e:
                    self.failed += 1
                    print(f"{name} FAILED with exception: {e}")
                    import traceback
                    traceback.print_exc()

        finally:
            self.cleanup()

        print('\n' + '=' * 60)
        print(' ' * 20 + "TEST SUMMARY")
        print('=' * 60)
        print(f"Passed: {self.passed}/{self.passed + self.failed}")
        print(f"failed: {self.failed}/{self.passed + self.failed}")

        if self.failed == 0:
            print("\nAll tests passed!")
            sys.exit(0)
        else:
            print(f"\n{self.failed} test(s) failed")
            sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("ERROR: 'backup.py' path not provided", file=sys.stderr)
        sys.exit(1)

    backup_py_path = Path(sys.argv[1])
    test_suite = TestBackup(backup_py_path)
    test_suite.run_all_tests()

if __name__ == "__main__":
    main()
