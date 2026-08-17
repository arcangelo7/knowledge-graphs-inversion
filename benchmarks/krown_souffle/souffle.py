# SPDX-FileCopyrightText: 2024-2026 Ali Elhalawati <alihalawaty@gmail.com>
#
# SPDX-License-Identifier: MIT

"""
The Souffle reasoner.
"""

import os
import psutil
import threading
import tempfile
import shutil
from typing import Optional
from bench_executor.container import Container
from bench_executor.logger import Logger

VERSION = '1.0.0'

TIMEOUT = 3 * 3600  # 3 hours



class Souffle(Container):
    """Souffle container for executing R2RML and RML mappings."""

    def __init__(self, data_path: str, config_path: str, directory: str,
                 verbose: bool):
        """Creates an instance of the Souffle class.

        Parameters
        ----------
        data_path : str
            Path to the data directory of the case.
        config_path : str
            Path to the config directory of the case.
        directory : str
            Path to the directory to store logs.
        verbose : bool
            Enable verbose logs.
        """
        self._data_path = os.path.abspath(data_path)
        self._config_path = os.path.abspath(config_path)
        self._logger = Logger(__name__, directory, verbose)
        self._verbose = verbose

        # Use a Linux temp directory for the Souffle data volume so the mount
        # never touches the Windows-mounted case path (avoids DrvFs/NTFS
        # case-folding conflicts with the data-generator/Souffle/ parent).
        self._souffle_tmp = tempfile.mkdtemp(prefix='krown_souffle_')
        super().__init__(f'alloka/souffle:v{VERSION}', 'Souffle',
                         self._logger,
                 volumes=[f'{self._souffle_tmp}:/data',
                                  f'{self._data_path}/shared:/data/shared'])
        print('INIT')

    @property
    def root_mount_directory(self) -> str:
        """Subdirectory in the root directory of the case for Souffle.

        Returns
        -------
        subdirectory : str
            Subdirectory of the root directory for Souffle.

        """
        return __name__.lower()

    def stop(self) -> bool:
        """Stop the Souffle container and clean up the temp data directory."""
        result = super().stop()
        if hasattr(self, '_souffle_tmp') and os.path.isdir(self._souffle_tmp):
            shutil.rmtree(self._souffle_tmp, ignore_errors=True)
        return result

    def _execute_with_timeout(self, command: str) -> bool:
        self._logger.info(f'Executing Souffle command: {command}')
        result = [False]
        exc_box = [None]

        def _run():
            try:
                result[0] = self.run_and_wait_for_exit(command)
            except Exception as exc:
                exc_box[0] = exc

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(TIMEOUT)
        if t.is_alive():
            self._logger.warning(f'Timeout ({TIMEOUT}s) reached for Souffle')
            return False
        if exc_box[0] is not None:
            raise exc_box[0]
        return result[0]


    def execute(self, arguments: list) -> bool:
        """Execute Souffle with given arguments.

        Parameters
        ----------
        arguments : list
            Arguments to supply to Souffle.

        Returns
        -------
        success : bool
            Whether the execution succeeded or not.
        """
        command = ' '.join(arguments)
        return self._execute_with_timeout(command)

    def execute_mapping(self, mapping_file: str, output_file: str,
                        serialization: str,
                        rdb_username: Optional[str] = None,
                        rdb_password: Optional[str] = None,
                        rdb_host: Optional[str] = None,
                        rdb_port: Optional[int] = None,
                        rdb_name: Optional[str] = None,
                        rdb_type: Optional[str] = None) -> bool:

        """Execute a First Order Logic mapping file with Souffle.

        Parameters
        ----------
        mapping_file : str
            Path to the mapping file to convert.
        output_file : str
            Name of the output file to store generated triples in.

        Returns
        -------
        success : bool
            Whether the execution was successfull or not.
        """

        del output_file  # currently unused by this runner
        del serialization  # currently unused by this runner

        max_heap = int(psutil.virtual_memory().total * (1/2))
        mapping_path = f"/data/shared/{mapping_file.replace('\\', '/').lstrip('/')}"
        forward_program_path = '/data/shared/Datalog_rules.rs'

        # Build rulegen command.
        arguments1: list[str] = []
        if rdb_username is not None and rdb_password is not None \
                and rdb_host is not None and rdb_port is not None \
                and rdb_name is not None and rdb_type is not None:

            arguments1.append('-u')
            arguments1.append(rdb_username)
            arguments1.append('-p')
            arguments1.append(rdb_password)

            parameters = ''
            if rdb_type == 'MySQL':
                protocol = 'jdbc:mysql'
                parameters = '?allowPublicKeyRetrieval=true&useSSL=false'
            elif rdb_type == 'PostgreSQL':
                protocol = 'jdbc:postgresql'
            else:
                raise ValueError(f'Unknown RDB type: "{rdb_type}"')
            rdb_dsn = f'\'{protocol}://{rdb_host}:{rdb_port}/' + \
                      f'{rdb_name}{parameters}\''
            arguments1.append('-dsn')
            arguments1.append(rdb_dsn)

        rulegen_suffix = ''
        if arguments1:
            rulegen_suffix = ' ' + ' '.join(arguments1)
        rulegen_cmd = (
            f'java -Xmx{max_heap} -Xms{max_heap} -jar rulegen.jar '
            f"-m '{mapping_path}'{rulegen_suffix}"
        )

        # Link bundled functors explicitly for consistent compile-mode behavior.
        souffle_cmd = (
            f"souffle -L /souffle/lib -l functors -c '{forward_program_path}' "
            f"-F /data/shared -D /data/shared"
        )
        full_cmd = f'bash -lc "{rulegen_cmd} && {souffle_cmd}"'
        if not self._execute_with_timeout(full_cmd):
            return False

        generated_program = os.path.join(self._data_path, 'shared', 'Datalog_rules.rs')
        if not os.path.exists(generated_program) or os.path.getsize(generated_program) == 0:
            self._logger.error(
                'Souffle rule generation did not produce a non-empty '
                'Datalog_rules.rs artifact'
            )
            return False

        return True
