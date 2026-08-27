# SPDX-FileCopyrightText: 2026 Ali Elhalawati <alihalawaty@gmail.com>
#
# SPDX-License-Identifier: MIT

"""
Reverse Souffle runner.

This resource keeps the forward Souffle runner unchanged and provides
an explicit reverse pipeline:
1) run rulegen.jar on the mapping file to generate forward Datalog,
2) run reverseR2RML.py to generate reverse Datalog (or forward+reverse
    artifacts for selective provenance),
3) run Souffle on the reverse Datalog using RDF inputs from /data/shared.
"""

import os
import shutil
import psutil
import threading
import tempfile
from typing import Optional
from bench_executor.container import Container
from bench_executor.logger import Logger

VERSION = '1.0.0'
TIMEOUT = 3 * 3600  # 3 hours


class ReverseSouffle(Container):
    """Souffle container for reverse R2RML execution."""

    _MARKER_PREFIX = '.reverse_souffle_'

    @staticmethod
    def _resolve_reverse_script(config_path: str) -> Optional[str]:
        candidates = [
            os.path.join(config_path, 'reverseR2RML.py'),
            os.path.join(os.getcwd(), 'reverseR2RML.py'),
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return None

    def __init__(self, data_path: str, config_path: str, directory: str,
                 verbose: bool):
        self._data_path = os.path.abspath(data_path)
        self._config_path = os.path.abspath(config_path)
        self._logger = Logger(__name__, directory, verbose)
        self._verbose = verbose
        self._reverse_script_host_path = self._resolve_reverse_script(self._config_path)
        self._reverse_script_container_path = '/souffle/reverseR2RML.py'

        # Use a Linux temp directory for the Souffle data volume so the mount
        # never touches the Windows-mounted case path (avoids DrvFs/NTFS
        # case-folding conflicts with the data-generator/Souffle/ parent).
        self._souffle_tmp = tempfile.mkdtemp(prefix='krown_rsouffle_')
        volumes = [f'{self._souffle_tmp}:/data',
                   f'{self._data_path}/shared:/data/shared']
        if self._reverse_script_host_path is not None:
            volumes.append(
                f'{os.path.dirname(self._reverse_script_host_path)}:/workspace-tools'
            )
            self._reverse_script_container_path = '/workspace-tools/reverseR2RML.py'
        super().__init__(f'alloka/souffle:v{VERSION}', 'ReverseSouffle',
                         self._logger,
                         volumes=volumes)

    @property
    def root_mount_directory(self) -> str:
        return __name__.lower()

    def stop(self) -> bool:
        """Stop the ReverseSouffle container and clean up the temp data directory."""
        result = super().stop()
        if hasattr(self, '_souffle_tmp') and os.path.isdir(self._souffle_tmp):
            shutil.rmtree(self._souffle_tmp, ignore_errors=True)
        return result

    def _execute_with_timeout(self, command: str) -> bool:
        self._logger.info(f'Executing ReverseSouffle command: {command}')
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
            self._logger.warning(f'Timeout ({TIMEOUT}s) reached for ReverseSouffle')
            return False
        if exc_box[0] is not None:
            raise exc_box[0]
        return result[0]

    def execute(self, arguments: list) -> bool:
        command = ' '.join(arguments)
        return self._execute_with_timeout(command)

    def _shared_host_path(self, relative_path: str) -> str:
        return os.path.join(self._data_path, 'shared', relative_path)

    def _marker_host_path(self, name: str) -> str:
        return self._shared_host_path(f'{self._MARKER_PREFIX}{name}')

    def _container_marker_path(self, name: str) -> str:
        return f'/data/shared/{self._MARKER_PREFIX}{name}'

    def _required_artifacts(self, reverse_program_file: str,
                            forward_program_file: str,
                            support_report: Optional[str],
                            include_forward_program: bool) -> list[tuple[str, str]]:
        artifacts = [
            ('reverse Datalog program', self._shared_host_path(reverse_program_file)),
        ]
        if include_forward_program:
            artifacts.append(
                ('forward Datalog program', self._shared_host_path(forward_program_file))
            )
        if support_report:
            artifacts.append(('support report', self._shared_host_path(support_report)))
        return artifacts

    def execute_forward_provenance(self, mapping_file: str, output_file: str,
                                   serialization: str,
                                   reverse_program_file: str = 'Datalog_reverse.rs',
                                   forward_program_file: str = 'Datalog_forward_with_prov.rs',
                                   support_report: Optional[str] = None,
                                   target_triples_file: Optional[str] = None,
                                   rdb_username: Optional[str] = None,
                                   rdb_password: Optional[str] = None,
                                   rdb_host: Optional[str] = None,
                                   rdb_port: Optional[int] = None,
                                   rdb_name: Optional[str] = None,
                                   rdb_type: Optional[str] = None) -> bool:
        """Run only the forward-provenance stage (no reverse Souffle execution).

        This method is useful when measuring forward-stage overhead in isolation.
        """
        return self.execute_mapping(
            mapping_file=mapping_file,
            output_file=output_file,
            serialization=serialization,
            reverse_program_file=reverse_program_file,
            forward_program_file=forward_program_file,
            support_report=support_report,
            souffle_mode='provenance',
            target_triples_file=target_triples_file,
            rdb_username=rdb_username,
            rdb_password=rdb_password,
            rdb_host=rdb_host,
            rdb_port=rdb_port,
            rdb_name=rdb_name,
            rdb_type=rdb_type,
            run_forward_stage=True,
            run_reverse_stage=False,
        )

    def execute_forward_hybrid(self, mapping_file: str, output_file: str,
                               serialization: str,
                               reverse_program_file: str = 'Datalog_reverse.rs',
                               forward_program_file: str = 'Datalog_forward_with_prov.rs',
                               support_report: Optional[str] = None,
                               rdb_username: Optional[str] = None,
                               rdb_password: Optional[str] = None,
                               rdb_host: Optional[str] = None,
                               rdb_port: Optional[int] = None,
                               rdb_name: Optional[str] = None,
                               rdb_type: Optional[str] = None) -> bool:
        return self.execute_mapping(
            mapping_file=mapping_file,
            output_file=output_file,
            serialization=serialization,
            reverse_program_file=reverse_program_file,
            forward_program_file=forward_program_file,
            support_report=support_report,
            souffle_mode='hybrid',
            rdb_username=rdb_username,
            rdb_password=rdb_password,
            rdb_host=rdb_host,
            rdb_port=rdb_port,
            rdb_name=rdb_name,
            rdb_type=rdb_type,
            run_forward_stage=True,
            run_reverse_stage=False,
        )

    def execute_reverse_only(self, mapping_file: str, output_file: str,
                             serialization: str,
                             reverse_program_file: str = 'Datalog_reverse.rs',
                             forward_program_file: str = 'Datalog_forward_with_prov.rs',
                             support_report: Optional[str] = None,
                             souffle_mode: str = 'rdf',
                             rdb_username: Optional[str] = None,
                             rdb_password: Optional[str] = None,
                             rdb_host: Optional[str] = None,
                             rdb_port: Optional[int] = None,
                             rdb_name: Optional[str] = None,
                             rdb_type: Optional[str] = None) -> bool:
        """Run only the reverse stage.

        The selected mode reads the inputs that its forward execution preserved.
        """
        return self.execute_mapping(
            mapping_file=mapping_file,
            output_file=output_file,
            serialization=serialization,
            reverse_program_file=reverse_program_file,
            forward_program_file=forward_program_file,
            support_report=support_report,
            souffle_mode=souffle_mode,
            target_triples_file=None,
            rdb_username=rdb_username,
            rdb_password=rdb_password,
            rdb_host=rdb_host,
            rdb_port=rdb_port,
            rdb_name=rdb_name,
            rdb_type=rdb_type,
            run_forward_stage=False,
            run_reverse_stage=True,
        )

    def _validate_artifacts(self, artifacts: list[tuple[str, str]],
                            stage_name: str) -> bool:
        missing_artifacts = [label for label, path in artifacts if not os.path.exists(path)]
        if missing_artifacts:
            self._logger.error(
                f'ReverseSouffle {stage_name} stage finished without required output artifacts. '
                f'Missing: {", ".join(missing_artifacts)}'
            )
            return False

        empty_artifacts = [
            label for label, path in artifacts
            if os.path.isfile(path) and os.path.getsize(path) == 0
        ]
        if empty_artifacts:
            self._logger.error(
                f'ReverseSouffle {stage_name} stage produced empty output artifacts. '
                f'Empty: {", ".join(empty_artifacts)}'
            )
            return False

        return True

    def _run_stage(self, stage_name: str, command: str, marker_name: str,
                   required_artifacts: Optional[list[tuple[str, str]]] = None) -> bool:
        marker_host_path = self._marker_host_path(marker_name)
        try:
            os.remove(marker_host_path)
        except FileNotFoundError:
            pass

        wrapped_command = (
            f'bash -lc "{command} && : > \"{self._container_marker_path(marker_name)}\""'
        )
        if not self._execute_with_timeout(wrapped_command):
            self._logger.error(f'ReverseSouffle {stage_name} stage failed')
            return False

        if not os.path.exists(marker_host_path):
            self._logger.error(
                f'ReverseSouffle did not complete the {stage_name} stage. '
                f'Missing completion marker: {marker_name}'
            )
            return False

        if required_artifacts:
            return self._validate_artifacts(required_artifacts, stage_name)
        return True

    def execute_mapping(self, mapping_file: str, output_file: str,
                        serialization: str,
                        reverse_program_file: str = 'Datalog_reverse.rs',
                        forward_program_file: str = 'Datalog_forward_with_prov.rs',
                        support_report: Optional[str] = None,
                        souffle_mode: str = 'rdf',
                        target_triples_file: Optional[str] = None,
                        run_forward_stage: bool = True,
                        run_reverse_stage: bool = True,
                        rdb_username: Optional[str] = None,
                        rdb_password: Optional[str] = None,
                        rdb_host: Optional[str] = None,
                        rdb_port: Optional[int] = None,
                        rdb_name: Optional[str] = None,
                        rdb_type: Optional[str] = None) -> bool:
        """Generate and execute reverse Datalog using Souffle.

        Parameters
        ----------
        mapping_file : str
            Input mapping file path relative to /data/shared.
        output_file : str
            Kept for compatibility with the execution framework metadata schema.
        serialization : str
            Kept for compatibility with the execution framework metadata schema.
        reverse_program_file : str
            Output reverse Datalog file path relative to /data/shared.
        forward_program_file : str
            Output forward Datalog file path relative to /data/shared.
        support_report : str, optional
            Optional JSON report output path relative to /data/shared.
        souffle_mode : str
            RDF, provenance, or hybrid inversion mode.
        target_triples_file : str, optional
            Optional tab-separated file (s, p, o) relative to /data/shared.
            Optional filter for forward provenance materialization. When
            provided, only listed triples receive provenance facts.
        run_forward_stage : bool
            When True, execute the selected forward Souffle stage.
        run_reverse_stage : bool
            When True, execute reverse Souffle stage.
        """
        del output_file  # currently unused in reverse mode
        del serialization  # currently unused in reverse mode

        if not run_forward_stage and not run_reverse_stage:
            raise ValueError('At least one stage must be enabled')

        if souffle_mode not in {'rdf', 'provenance', 'hybrid'}:
            raise ValueError(
                'souffle_mode must be rdf, provenance, or hybrid'
            )

        if target_triples_file and not run_forward_stage:
            raise ValueError(
                'target_triples_file is only supported when run_forward_stage=True'
            )

        if target_triples_file and souffle_mode != 'provenance':
            raise ValueError(
                'target_triples_file requires provenance mode'
            )

        required_artifacts = self._required_artifacts(
            reverse_program_file,
            forward_program_file,
            support_report,
            include_forward_program=(
                souffle_mode != 'rdf' and run_forward_stage
            ),
        )

        total_memory = psutil.virtual_memory().total
        # Keep headroom for Docker/Souffle while allowing larger JVM heaps.
        max_heap = int(total_memory * 0.75)
        # Use a smaller initial heap to avoid reserving the full max upfront.
        min_heap = int(total_memory * 0.25)

        normalized_mapping_file = mapping_file.replace('\\', '/').lstrip('/')
        normalized_reverse_program_file = (
            reverse_program_file.replace('\\', '/').lstrip('/')
        )
        normalized_forward_program_file = (
            forward_program_file.replace('\\', '/').lstrip('/')
        )

        mapping_path = f"/data/shared/{normalized_mapping_file}"
        forward_program_path = '/data/shared/Datalog_rules.rs'
        reverse_program_path = (
            f"/data/shared/{normalized_reverse_program_file}"
        )
        forward_program_path_out = (
            f"/data/shared/{normalized_forward_program_file}"
        )

        rulegen_args: list[str] = []
        if rdb_username is not None and rdb_password is not None \
                and rdb_host is not None and rdb_port is not None \
                and rdb_name is not None and rdb_type is not None:
            rulegen_args.extend(['-u', rdb_username, '-p', rdb_password])

            parameters = ''
            if rdb_type == 'MySQL':
                protocol = 'jdbc:mysql'
                parameters = '?allowPublicKeyRetrieval=true&useSSL=false'
            elif rdb_type == 'PostgreSQL':
                protocol = 'jdbc:postgresql'
            else:
                raise ValueError(f'Unknown RDB type: "{rdb_type}"')

            rdb_dsn = f"'{protocol}://{rdb_host}:{rdb_port}/{rdb_name}{parameters}'"
            rulegen_args.extend(['-dsn', rdb_dsn])

        # RDF-only reverse runs only need the generated program.
        # Skip facts emission to avoid unnecessary file generation work.
        if souffle_mode == 'rdf':
            rulegen_args.append('-nef')

        rulegen_suffix = ''
        if rulegen_args:
            rulegen_suffix = ' ' + ' '.join(rulegen_args)

        rulegen_cmd = (
            f'java -Xmx{max_heap} -Xms{min_heap} -jar rulegen.jar '
            f'-m "{mapping_path}"{rulegen_suffix}'
        )

        staged_target_path = None
        if target_triples_file:
            source_target_path = self._shared_host_path(target_triples_file)
            if not os.path.exists(source_target_path):
                raise FileNotFoundError(
                    f'target_triples_file not found: {source_target_path}'
                )
            staged_target_path = '__reverse_target_triples_input.csv'
            shutil.copyfile(
                source_target_path,
                self._shared_host_path(staged_target_path),
            )

        if souffle_mode == 'provenance' and run_forward_stage:
            reverse_cmd = (
                f'python3 {self._reverse_script_container_path} '
                f'"{forward_program_path}" "{forward_program_path_out}" '
                '--mode forward --with-provenance '
                f'--reverse-output "{reverse_program_path}"'
            )
            if target_triples_file:
                target_path = f'/data/shared/{staged_target_path}'
                reverse_cmd += f' --target-triples-file "{target_path}"'
        elif souffle_mode == 'hybrid':
            reverse_cmd = (
                f'python3 {self._reverse_script_container_path} '
                f'"{forward_program_path}" "{forward_program_path_out}" '
                f'--mode hybrid --reverse-output "{reverse_program_path}"'
            )
        else:
            reverse_mode_flag = (
                '--mode reverse --with-provenance'
                if souffle_mode == 'provenance'
                else '--mode reverse'
            )
            reverse_cmd = (
                f'python3 {self._reverse_script_container_path} '
                f'"{forward_program_path}" "{reverse_program_path}" {reverse_mode_flag}'
            )

        if support_report:
            normalized_support_report = support_report.replace('\\', '/').lstrip('/')
            support_path = f"/data/shared/{normalized_support_report}"
            reverse_cmd += f' --support-report "{support_path}"'

        # Execute the generated forward program directly so the
        # container lifecycle tracks the actual Souffle process end-to-end.
        forward_souffle_cmd = (
            f'cd /data/shared && souffle -L /souffle/lib -l functors -c '
            f'"{forward_program_path_out}" -F /data/shared -D /data/shared'
        )

        # Ensure the reverse stage consumes the RDF outputs generated by the
        # forward provenance run. If the forward run did not emit them, create
        # empty placeholders so the reverse program can still be executed.
        input_bridge_cmd = (
            'cd /data/shared && '
            'if [ -f triple.csv ]; then :; '
            'elif [ -f triple.facts ]; then cp triple.facts triple.csv; '
            'else : > triple.csv; fi && '
            'if [ -f quadruple.csv ]; then :; '
            'elif [ -f quadruple.facts ]; then cp quadruple.facts quadruple.csv; '
            'else : > quadruple.csv; fi'
        )

        # Map forward provenance outputs to the filenames expected by reverse.
        # If a provenance file is absent, create an empty placeholder so
        # Souffle .input has a concrete file to read.
        provenance_bridge_cmd = (
            'cd /data/shared && '
            'if [ -f ExplainContributor.csv ]; then cp ExplainContributor.csv ProvContributor.csv; '
            'elif [ -f ExplainContributor.facts ]; then cp ExplainContributor.facts ProvContributor.csv; '
            'else : > ProvContributor.csv; fi && '
            'if [ -f ExplainQuadContributor.csv ]; then cp ExplainQuadContributor.csv ProvQuadContributor.csv; '
            'elif [ -f ExplainQuadContributor.facts ]; then cp ExplainQuadContributor.facts ProvQuadContributor.csv; '
            'else : > ProvQuadContributor.csv; fi'
        )

        # Execute the generated reverse program directly for the same reason.
        reverse_souffle_cmd = (
            f'cd /data/shared && souffle -L /souffle/lib -l functors -c '
            f'"{reverse_program_path}" -F /data/shared -D /data/shared'
        )

        if not self._run_stage(
            'rule generation',
            rulegen_cmd,
            'rulegen_done',
            [('forward Datalog program', self._shared_host_path('Datalog_rules.rs'))],
        ):
            return False

        if not self._run_stage(
            'program generation',
            reverse_cmd,
            'codegen_done',
            required_artifacts,
        ):
            return False

        if souffle_mode != 'rdf' and run_forward_stage:
            if not self._run_stage(
                f'forward {souffle_mode} execution',
                forward_souffle_cmd,
                'forward_done',
            ):
                return False

            if souffle_mode == 'provenance':
                if not self._run_stage(
                    'input bridging',
                    f'{input_bridge_cmd} && {provenance_bridge_cmd}',
                    'bridge_done',
                ):
                    return False

        if run_reverse_stage:
            if not self._run_stage(
                'reverse execution',
                reverse_souffle_cmd,
                'reverse_done',
            ):
                return False

        return True
