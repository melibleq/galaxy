"""
Universe configuration builder.
"""
# absolute_import needed for tool_shed package.

import collections
import configparser
import errno
import ipaddress
import logging
import logging.config
import os
import re
import signal
import socket
import string
import sys
import tempfile
import threading
import time
from datetime import timedelta
from typing import Dict, Optional, Set

import yaml
from beaker.cache import CacheManager
from beaker.util import parse_cache_config_options

from galaxy.config.schema import AppSchema
from galaxy.containers import parse_containers_config
from galaxy.exceptions import ConfigurationError
from galaxy.model import mapping
from galaxy.model.database_utils import database_exists
from galaxy.model.tool_shed_install.migrate.check import create_or_verify_database as tsi_create_or_verify_database
from galaxy.util import (
    ExecutionTimer,
    listify,
    string_as_bool,
    unicodify,
)
from galaxy.util.custom_logging import LOGLV_TRACE
from galaxy.util.dbkeys import GenomeBuilds
from galaxy.util.properties import (
    find_config_file,
    read_properties_from_file,
    running_from_source,
)
from galaxy.web.formatting import expand_pretty_datetime_format
from galaxy.web_stack import (
    get_stack_facts,
    register_postfork_function
)
from ..version import VERSION_MAJOR, VERSION_MINOR

log = logging.getLogger(__name__)

GALAXY_APP_NAME = 'galaxy'
GALAXY_CONFIG_SCHEMA_PATH = 'lib/galaxy/webapps/galaxy/config_schema.yml'
LOGGING_CONFIG_DEFAULT = {
    'disable_existing_loggers': False,
    'version': 1,
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
    'loggers': {
        'paste.httpserver.ThreadPool': {
            'level': 'WARN',
            'qualname': 'paste.httpserver.ThreadPool',
        },
        'sqlalchemy_json.track': {
            'level': 'WARN',
            'qualname': 'sqlalchemy_json.track',
        },
        'urllib3.connectionpool': {
            'level': 'WARN',
            'qualname': 'urllib3.connectionpool',
        },
        'routes.middleware': {
            'level': 'WARN',
            'qualname': 'routes.middleware',
        },
        'amqp': {
            'level': 'INFO',
            'qualname': 'amqp',
        },
        'botocore': {
            'level': 'INFO',
            'qualname': 'botocore',
        },
    },
    'filters': {
        'stack': {
            '()': 'galaxy.web_stack.application_stack_log_filter',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'stack',
            'level': 'DEBUG',
            'stream': 'ext://sys.stderr',
            'filters': ['stack'],
        },
    },
    'formatters': {
        'stack': {
            '()': 'galaxy.web_stack.application_stack_log_formatter',
        },
    },
}
"""Default value for logging configuration, passed to :func:`logging.config.dictConfig`"""


def find_root(kwargs):
    return os.path.abspath(kwargs.get('root_dir', '.'))


class BaseAppConfiguration:
    # Override in subclasses (optional): {KEY: config option, VALUE: deprecated directory name}
    # If VALUE == first directory in a user-supplied path that resolves to KEY, it will be stripped from that path
    renamed_options: Optional[Dict[str, str]] = None
    deprecated_dirs: Dict[str, str] = {}
    paths_to_check_against_root: Set[str] = set()  # backward compatibility: if resolved path doesn't exist, try resolving w.r.t root
    add_sample_file_to_defaults: Set[str] = set()  # for these options, add sample config files to their defaults
    listify_options: Set[str] = set()  # values for these options are processed as lists of values

    def __init__(self, **kwargs):
        self._preprocess_kwargs(kwargs)
        self._kwargs = kwargs  # Save these as a record of explicitly set options
        self.config_dict = kwargs
        self.root = find_root(kwargs)
        self._set_config_base(kwargs)
        self.schema = self._load_schema()  # Load schema from schema definition file
        self._raw_config = self.schema.defaults.copy()  # Save schema defaults as initial config values (raw_config)
        self._update_raw_config_from_kwargs(kwargs)  # Overwrite raw_config with values passed in kwargs
        self._create_attributes_from_raw_config()  # Create attributes based on raw_config
        self._preprocess_paths_to_resolve()  # Any preprocessing steps that need to happen before paths are resolved
        self._resolve_paths()  # Overwrite attribute values with resolved paths
        self._postprocess_paths_to_resolve()  # Any steps that need to happen after paths are resolved

    def _preprocess_kwargs(self, kwargs):
        self._process_renamed_options(kwargs)
        self._fix_postgresql_dburl(kwargs)

    def _process_renamed_options(self, kwargs):
        """Update kwargs to set any unset renamed options to values of old-named options, if set.

        Does not remove the old options from kwargs so that deprecated option usage can be logged.
        """
        if self.renamed_options is not None:
            for old, new in self.renamed_options.items():
                if old in kwargs and new not in kwargs:
                    kwargs[new] = kwargs[old]

    def _fix_postgresql_dburl(self, kwargs):
        """
        Fix deprecated database URLs (postgres... >> postgresql...)
        https://docs.sqlalchemy.org/en/14/changelog/changelog_14.html#change-3687655465c25a39b968b4f5f6e9170b
        """
        old_dialect, new_dialect = 'postgres', 'postgresql'
        old_prefixes = (f'{old_dialect}:', f'{old_dialect}+')  # check for postgres://foo and postgres+driver//foo
        offset = len(old_dialect)
        keys = ('database_connection', 'install_database_connection')
        for key in keys:
            if key in kwargs:
                value = kwargs[key]
                for prefix in old_prefixes:
                    if value.startswith(prefix):
                        value = f'{new_dialect}{value[offset:]}'
                        kwargs[key] = value
                        log.warning('PostgreSQL database URLs of the form "postgres://" have been '
                            'deprecated. Please use "postgresql://".')

    def is_set(self, key):
        """Check if a configuration option has been explicitly set."""
        # NOTE: This will check all supplied keyword arguments, including those not in the schema.
        # To check only schema options, change the line below to `if property not in self._raw_config:`
        if key not in self._raw_config:
            log.warning(f"Configuration option does not exist: '{key}'")
        return key in self._kwargs

    def resolve_path(self, path):
        """Resolve a path relative to Galaxy's root."""
        return self._in_root_dir(path)

    def _set_config_base(self, config_kwargs):

        def _set_global_conf():
            self.config_file = find_config_file('galaxy')
            self.global_conf = config_kwargs.get('global_conf')
            self.global_conf_parser = configparser.ConfigParser()
            if not self.config_file and self.global_conf and "__file__" in self.global_conf:
                self.config_file = os.path.join(self.root, self.global_conf['__file__'])

            if self.config_file is None:
                log.warning("No Galaxy config file found, running from current working directory: %s", os.getcwd())
            else:
                try:
                    self.global_conf_parser.read(self.config_file)
                except OSError:
                    raise
                except Exception:
                    pass  # Not an INI file

        def _set_config_directories():
            # Set config_dir to value from kwargs OR dirname of config_file OR None
            _config_dir = os.path.dirname(self.config_file) if self.config_file else None
            self.config_dir = config_kwargs.get('config_dir', _config_dir)
            # Make path absolute before using it as base for other paths
            if self.config_dir:
                self.config_dir = os.path.abspath(self.config_dir)

            self.data_dir = config_kwargs.get('data_dir')
            if self.data_dir:
                self.data_dir = os.path.abspath(self.data_dir)

            self.sample_config_dir = os.path.join(os.path.dirname(__file__), 'sample')
            if self.sample_config_dir:
                self.sample_config_dir = os.path.abspath(self.sample_config_dir)

            self.managed_config_dir = config_kwargs.get('managed_config_dir')
            if self.managed_config_dir:
                self.managed_config_dir = os.path.abspath(self.managed_config_dir)

            if running_from_source:
                if not self.config_dir:
                    self.config_dir = os.path.join(self.root, 'config')
                if not self.data_dir:
                    self.data_dir = os.path.join(self.root, 'database')
                if not self.managed_config_dir:
                    self.managed_config_dir = self.config_dir
            else:
                if not self.config_dir:
                    self.config_dir = os.getcwd()
                if not self.data_dir:
                    self.data_dir = self._in_config_dir('data')
                if not self.managed_config_dir:
                    self.managed_config_dir = self._in_data_dir('config')

            # TODO: do we still need to support ../shed_tools when running_from_source?
            self.shed_tools_dir = self._in_data_dir('shed_tools')

            log.debug("Configuration directory is %s", self.config_dir)
            log.debug("Data directory is %s", self.data_dir)
            log.debug("Managed config directory is %s", self.managed_config_dir)

        _set_global_conf()
        _set_config_directories()

    def _load_schema(self):
        # Override in subclasses
        raise Exception('Not implemented')

    def _preprocess_paths_to_resolve(self):
        # For these options, if option is not set, listify its defaults and add a sample config file.
        if self.add_sample_file_to_defaults:
            for key in self.add_sample_file_to_defaults:
                if not self.is_set(key):
                    defaults = listify(getattr(self, key), do_strip=True)
                    sample = f'{defaults[-1]}.sample'  # if there are multiple defaults, use last as template
                    sample = self._in_sample_dir(sample)  # resolve w.r.t sample_dir
                    defaults.append(sample)
                    setattr(self, key, defaults)

    def _postprocess_paths_to_resolve(self):

        def select_one_path_from_list():
            # To consider: options with a sample file added to defaults except options that can have multiple values.
            # If value is not set, check each path in list; set to first path that exists; if none exist, set to last path in list.
            keys = self.add_sample_file_to_defaults - self.listify_options if self.listify_options else self.add_sample_file_to_defaults
            for key in keys:
                if not self.is_set(key):
                    paths = getattr(self, key)
                    for path in paths:
                        if self._path_exists(path):
                            setattr(self, key, path)
                            break
                    else:
                        setattr(self, key, paths[-1])  # TODO: we assume it exists; but we've already checked in the loop! Raise error instead?

        def select_one_or_all_paths_from_list():
            # Values for these options are lists of paths. If value is not set, use defaults if all paths in list exist;
            # otherwise, set to last path in list.
            for key in self.listify_options:
                if not self.is_set(key):
                    paths = getattr(self, key)
                    for path in paths:
                        if not self._path_exists(path):
                            setattr(self, key, [paths[-1]])  # value is a list
                            break

        if self.add_sample_file_to_defaults:  # Currently, this is the ONLY case when we need to pick one file from a list
            select_one_path_from_list()
        if self.listify_options:
            select_one_or_all_paths_from_list()

    def _path_exists(self, path):  # factored out so we can mock it in tests
        return os.path.exists(path)

    def _set_alt_paths(self, option, *alt_paths):
        # If `option` is not set, check *alt_paths. Set `option` to first path that exists and return it.
        if not self.is_set(option):
            for path in alt_paths:
                if self._path_exists(path):
                    setattr(self, option, path)
                    return path

    def _update_raw_config_from_kwargs(self, kwargs):

        def convert_datatype(key, value):
            datatype = self.schema.app_schema[key].get('type')
            # check for `not None` explicitly (value can be falsy)
            if value is not None and datatype in type_converters:
                # convert value or each item in value to type `datatype`
                f = type_converters[datatype]
                if isinstance(value, list):
                    return [f(item) for item in value]
                else:
                    return f(value)
            return value

        def strip_deprecated_dir(key, value):
            resolves_to = self.schema.paths_to_resolve.get(key)
            if resolves_to:  # value contains paths that will be resolved
                paths = listify(value, do_strip=True)
                for i, path in enumerate(paths):
                    first_dir = path.split(os.sep)[0]  # get first directory component
                    if first_dir == self.deprecated_dirs.get(resolves_to):  # first_dir is deprecated for this option
                        ignore = first_dir + os.sep
                        log.warning(
                            "Paths for the '%s' option are now relative to '%s', remove the leading '%s' "
                            "to suppress this warning: %s", key, resolves_to, ignore, path
                        )
                        paths[i] = path[len(ignore):]

                # return list or string, depending on type of `value`
                if isinstance(value, list):
                    return paths
                return ','.join(paths)
            return value

        type_converters = {'bool': string_as_bool, 'int': int, 'float': float, 'str': str}

        for key, value in kwargs.items():
            if key in self.schema.app_schema:
                value = convert_datatype(key, value)
                if value and self.deprecated_dirs:
                    value = strip_deprecated_dir(key, value)
                self._raw_config[key] = value

    def _create_attributes_from_raw_config(self):
        # `base_configs` are a special case: these attributes have been created and will be ignored
        # by the code below. Trying to overwrite any other existing attributes will raise an error.
        base_configs = {'config_dir', 'data_dir', 'managed_config_dir'}
        for key, value in self._raw_config.items():
            if not hasattr(self, key):
                setattr(self, key, value)
            elif key not in base_configs:
                raise ConfigurationError(f"Attempting to override existing attribute '{key}'")

    def _resolve_paths(self):

        def resolve(key):
            if key in _cache:  # resolve each path only once
                return _cache[key]

            path = getattr(self, key)  # path prior to being resolved
            parent = self.schema.paths_to_resolve.get(key)
            if not parent:  # base case: nothing else needs resolving
                return path
            parent_path = resolve(parent)  # recursively resolve parent path
            if path is not None:
                path = os.path.join(parent_path, path)  # resolve path
            else:
                path = parent_path  # or use parent path

            setattr(self, key, path)  # update property
            _cache[key] = path  # cache it!
            return path

        _cache = {}
        for key in self.schema.paths_to_resolve:
            value = getattr(self, key)
            # Check if value is a list or should be listified; if so, listify and resolve each item separately.
            if type(value) is list or (self.listify_options and key in self.listify_options):
                saved_values = listify(getattr(self, key), do_strip=True)  # listify and save original value
                setattr(self, key, '_')  # replace value with temporary placeholder
                resolve(key)  # resolve temporary value (`_` becomes `parent-path/_`)
                resolved_base = getattr(self, key)[:-1]  # get rid of placeholder in resolved path
                # apply resolved base to saved values
                resolved_paths = [os.path.join(resolved_base, value) for value in saved_values]
                setattr(self, key, resolved_paths)  # set config.key to a list of resolved paths
            else:
                resolve(key)
            # Check options that have been set and may need to be resolved w.r.t. root
            if self.is_set(key) and self.paths_to_check_against_root and key in self.paths_to_check_against_root:
                self._check_against_root(key)

    def _check_against_root(self, key):

        def get_path(current_path, initial_path):
            # if path does not exist and was set as relative:
            if not self._path_exists(current_path) and not os.path.isabs(initial_path):
                new_path = self._in_root_dir(initial_path)
                if self._path_exists(new_path):  # That's a bingo!
                    resolves_to = self.schema.paths_to_resolve.get(key)
                    log.warning(
                        "Paths for the '{0}' option should be relative to '{1}'. To suppress this warning, "
                        "move '{0}' into '{1}', or set it's value to an absolute path.".format(key, resolves_to)
                    )
                    return new_path
            return current_path

        current_value = getattr(self, key)  # resolved path or list of resolved paths
        if type(current_value) is list:
            initial_paths = listify(self._raw_config[key], do_strip=True)  # initial unresolved paths
            updated_paths = []
            # check and, if needed, update each path in the list
            for current_path, initial_path in zip(current_value, initial_paths):
                path = get_path(current_path, initial_path)
                updated_paths.append(path)  # add to new list regardless of whether path has changed or not
            setattr(self, key, updated_paths)  # update: one or more paths may have changed
        else:
            initial_path = self._raw_config[key]  # initial unresolved path
            path = get_path(current_value, initial_path)
            if path != current_value:
                setattr(self, key, path)  # update if path has changed

    def _in_root_dir(self, path):
        return self._in_dir(self.root, path)

    def _in_managed_config_dir(self, path):
        return self._in_dir(self.managed_config_dir, path)

    def _in_config_dir(self, path):
        return self._in_dir(self.config_dir, path)

    def _in_sample_dir(self, path):
        return self._in_dir(self.sample_config_dir, path)

    def _in_data_dir(self, path):
        return self._in_dir(self.data_dir, path)

    def _in_dir(self, _dir, path):
        return os.path.join(_dir, path) if path else None


class CommonConfigurationMixin:
    """Shared configuration settings code for Galaxy and ToolShed."""

    @property
    def admin_users(self):
        return self._admin_users

    @admin_users.setter
    def admin_users(self, value):
        self._admin_users = value
        self.admin_users_list = listify(value)

    def is_admin_user(self, user):
        """Determine if the provided user is listed in `admin_users`."""
        return user and (user.email in self.admin_users_list or user.bootstrap_admin_user)

    @property
    def sentry_dsn_public(self):
        """
        Sentry URL with private key removed for use in client side scripts,
        sentry server will need to be configured to accept events
        """
        if self.sentry_dsn:
            return re.sub(r"^([^:/?#]+:)?//(\w+):(\w+)", r"\1//\2", self.sentry_dsn)

    def get_bool(self, key, default):
        # Warning: the value of self.config_dict['foo'] may be different from self.foo
        if key in self.config_dict:
            return string_as_bool(self.config_dict[key])
        else:
            return default

    def get(self, key, default=None):
        # Warning: the value of self.config_dict['foo'] may be different from self.foo
        return self.config_dict.get(key, default)

    def _ensure_directory(self, path):
        if path not in [None, False] and not os.path.isdir(path):
            try:
                os.makedirs(path)
            except Exception as e:
                raise ConfigurationError(f"Unable to create missing directory: {path}\n{unicodify(e)}")


class GalaxyAppConfiguration(BaseAppConfiguration, CommonConfigurationMixin):
    deprecated_options = ('database_file', 'track_jobs_in_database', 'blacklist_file', 'whitelist_file',
                          'sanitize_whitelist_file', 'user_library_import_symlink_whitelist', 'fetch_url_whitelist',
                          'containers_resolvers_config_file')
    renamed_options = {
        'blacklist_file': 'email_domain_blocklist_file',
        'whitelist_file': 'email_domain_allowlist_file',
        'sanitize_whitelist_file': 'sanitize_allowlist_file',
        'user_library_import_symlink_whitelist': 'user_library_import_symlink_allowlist',
        'fetch_url_whitelist': 'fetch_url_allowlist',
        'containers_resolvers_config_file': 'container_resolvers_config_file',
    }
    default_config_file_name = 'galaxy.yml'
    deprecated_dirs = {'config_dir': 'config', 'data_dir': 'database'}

    paths_to_check_against_root = {
        'auth_config_file',
        'build_sites_config_file',
        'containers_config_file',
        'data_manager_config_file',
        'datatypes_config_file',
        'dependency_resolvers_config_file',
        'error_report_file',
        'job_config_file',
        'job_metrics_config_file',
        'job_resource_params_file',
        'local_conda_mapping_file',
        'migrated_tools_config',
        'modules_mapping_files',
        'object_store_config_file',
        'oidc_backends_config_file',
        'oidc_config_file',
        'shed_data_manager_config_file',
        'shed_tool_config_file',
        'shed_tool_data_table_config',
        'tool_destinations_config_file',
        'tool_sheds_config_file',
        'user_preferences_extra_conf_path',
        'workflow_resource_params_file',
        'workflow_schedulers_config_file',
        'markdown_export_css',
        'markdown_export_css_pages',
        'markdown_export_css_invocation_reports',
        'file_path',
        'tool_data_table_config_path',
        'tool_config_file',
    }

    add_sample_file_to_defaults = {
        'build_sites_config_file',
        'datatypes_config_file',
        'job_metrics_config_file',
        'tool_data_table_config_path',
        'tool_config_file',
    }

    listify_options = {
        'tool_data_table_config_path',
        'tool_config_file',
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._override_tempdir(kwargs)
        self._process_config(kwargs)

    def _load_schema(self):
        # Schemas are symlinked to the root of the galaxy-app package
        config_schema_path = os.path.join(os.path.dirname(__file__), os.pardir, 'config_schema.yml')
        if os.path.exists(GALAXY_CONFIG_SCHEMA_PATH):
            config_schema_path = GALAXY_CONFIG_SCHEMA_PATH
        return AppSchema(config_schema_path, GALAXY_APP_NAME)

    def _override_tempdir(self, kwargs):
        if string_as_bool(kwargs.get("override_tempdir", "True")):
            tempfile.tempdir = self.new_file_path

    def config_value_for_host(self, config_option, host):
        val = getattr(self, config_option)
        if config_option in self.schema.per_host_options:
            per_host_option = f"{config_option}_by_host"
            if per_host_option in self.config_dict:
                per_host = self.config_dict[per_host_option] or {}
                for host_key, host_val in per_host.items():
                    if host_key in host:
                        val = host_val
                        break

        return val

    def _process_config(self, kwargs):
        # Backwards compatibility for names used in too many places to fix
        self.datatypes_config = self.datatypes_config_file
        self.tool_configs = self.tool_config_file

        # Collect the umask and primary gid from the environment
        self.umask = os.umask(0o77)  # get the current umask
        os.umask(self.umask)  # can't get w/o set, so set it back
        self.gid = os.getgid()  # if running under newgrp(1) we'll need to fix the group of data created on the cluster

        self.version_major = VERSION_MAJOR
        self.version_minor = VERSION_MINOR

        # Database related configuration
        self.check_migrate_databases = kwargs.get('check_migrate_databases', True)
        if not self.database_connection:  # Provide default if not supplied by user
            db_path = self._in_data_dir('universe.sqlite')
            self.database_connection = f'sqlite:///{db_path}?isolation_level=IMMEDIATE'
        self.database_engine_options = get_database_engine_options(kwargs)
        self.database_create_tables = string_as_bool(kwargs.get('database_create_tables', 'True'))
        self.database_encoding = kwargs.get('database_encoding')  # Create new databases with this encoding
        self.thread_local_log = None
        if self.enable_per_request_sql_debugging:
            self.thread_local_log = threading.local()
        # Install database related configuration (if different)
        self.install_database_engine_options = get_database_engine_options(kwargs, model_prefix="install_")
        self.shared_home_dir = kwargs.get("shared_home_dir")
        self.cookie_path = kwargs.get("cookie_path")
        self.tool_path = self._in_root_dir(self.tool_path)
        self.tool_data_path = self._in_root_dir(self.tool_data_path)
        if not running_from_source and kwargs.get("tool_data_path") is None:
            self.tool_data_path = self._in_data_dir(self.schema.defaults['tool_data_path'])
        self.builds_file_path = os.path.join(self.tool_data_path, self.builds_file_path)
        self.len_file_path = os.path.join(self.tool_data_path, self.len_file_path)
        self.oidc = {}
        self.integrated_tool_panel_config = self._in_managed_config_dir(self.integrated_tool_panel_config)
        integrated_tool_panel_tracking_directory = kwargs.get('integrated_tool_panel_tracking_directory')
        if integrated_tool_panel_tracking_directory:
            self.integrated_tool_panel_tracking_directory = self._in_root_dir(integrated_tool_panel_tracking_directory)
        else:
            self.integrated_tool_panel_tracking_directory = None
        self.toolbox_filter_base_modules = listify(self.toolbox_filter_base_modules)
        self.tool_filters = listify(self.tool_filters, do_strip=True)
        self.tool_label_filters = listify(self.tool_label_filters, do_strip=True)
        self.tool_section_filters = listify(self.tool_section_filters, do_strip=True)

        self.user_tool_filters = listify(self.user_tool_filters, do_strip=True)
        self.user_tool_label_filters = listify(self.user_tool_label_filters, do_strip=True)
        self.user_tool_section_filters = listify(self.user_tool_section_filters, do_strip=True)
        self.has_user_tool_filters = bool(self.user_tool_filters or self.user_tool_label_filters or self.user_tool_section_filters)

        self.password_expiration_period = timedelta(days=int(self.password_expiration_period))

        if self.shed_tool_data_path:
            self.shed_tool_data_path = self._in_root_dir(self.shed_tool_data_path)
        else:
            self.shed_tool_data_path = self.tool_data_path

        self.running_functional_tests = string_as_bool(kwargs.get('running_functional_tests', False))
        if isinstance(self.hours_between_check, str):
            self.hours_between_check = float(self.hours_between_check)
        try:
            if isinstance(self.hours_between_check, int):
                if self.hours_between_check < 1 or self.hours_between_check > 24:
                    self.hours_between_check = 12
            elif isinstance(self.hours_between_check, float):
                # If we're running functional tests, the minimum hours between check should be reduced to 0.001, or 3.6 seconds.
                if self.running_functional_tests:
                    if self.hours_between_check < 0.001 or self.hours_between_check > 24.0:
                        self.hours_between_check = 12.0
                else:
                    if self.hours_between_check < 1.0 or self.hours_between_check > 24.0:
                        self.hours_between_check = 12.0
            else:
                self.hours_between_check = 12
        except Exception:
            self.hours_between_check = 12
        self.update_integrated_tool_panel = kwargs.get("update_integrated_tool_panel", True)
        self.galaxy_data_manager_data_path = self.galaxy_data_manager_data_path or self.tool_data_path
        self.tool_secret = kwargs.get("tool_secret", "")
        self.metadata_strategy = kwargs.get("metadata_strategy", "directory")
        self.use_remote_user = self.use_remote_user or self.single_user
        self.fetch_url_allowlist_ips = [
            ipaddress.ip_network(unicodify(ip.strip()))  # If it has a slash, assume 127.0.0.1/24 notation
            if '/' in ip else
            ipaddress.ip_address(unicodify(ip.strip()))  # Otherwise interpret it as an ip address.
            for ip in kwargs.get("fetch_url_allowlist", "").split(',')
            if len(ip.strip()) > 0
        ]
        self.template_path = self._in_root_dir(kwargs.get("template_path", "templates"))
        self.job_queue_cleanup_interval = int(kwargs.get("job_queue_cleanup_interval", "5"))
        self.cluster_files_directory = self._in_root_dir(self.cluster_files_directory)

        # Fall back to legacy job_working_directory config variable if set.
        self.jobs_directory = self._in_data_dir(kwargs.get("jobs_directory", self.job_working_directory))
        if self.preserve_python_environment not in ["legacy_only", "legacy_and_local", "always"]:
            log.warning("preserve_python_environment set to unknown value [%s], defaulting to legacy_only")
            self.preserve_python_environment = "legacy_only"
        self.nodejs_path = kwargs.get("nodejs_path")
        self.container_image_cache_path = self._in_data_dir(kwargs.get("container_image_cache_path", "container_cache"))
        self.output_size_limit = int(kwargs.get('output_size_limit', 0))
        # activation_email was used until release_15.03
        activation_email = kwargs.get('activation_email')
        self.email_from = self.email_from or activation_email

        self.email_domain_blocklist_content = self._load_list_from_file(self._in_config_dir(self.email_domain_blocklist_file)) if self.email_domain_blocklist_file else None
        self.email_domain_allowlist_content = self._load_list_from_file(self._in_config_dir(self.email_domain_allowlist_file)) if self.email_domain_allowlist_file else None

        # These are not even beta - just experiments - don't use them unless
        # you want yours tools to be broken in the future.
        self.enable_beta_tool_formats = string_as_bool(kwargs.get('enable_beta_tool_formats', 'False'))

        if self.workflow_resource_params_mapper and ':' not in self.workflow_resource_params_mapper:
            # Assume it is not a Python function, so a file; else: a Python function
            self.workflow_resource_params_mapper = self._in_root_dir(self.workflow_resource_params_mapper)

        self.pbs_application_server = kwargs.get('pbs_application_server', "")
        self.pbs_dataset_server = kwargs.get('pbs_dataset_server', "")
        self.pbs_dataset_path = kwargs.get('pbs_dataset_path', "")
        self.pbs_stage_path = kwargs.get('pbs_stage_path', "")

        _sanitize_allowlist_path = self._in_managed_config_dir(self.sanitize_allowlist_file)
        if not os.path.isfile(_sanitize_allowlist_path):  # then check old default location
            for deprecated in (
                    self._in_managed_config_dir('sanitize_whitelist.txt'),
                    self._in_root_dir('config/sanitize_whitelist.txt')):
                if os.path.isfile(deprecated):
                    log.warning("The path '%s' for the 'sanitize_allowlist_file' config option is "
                        "deprecated and will be no longer checked in a future release. Please consult "
                        "the latest version of the sample configuration file." % deprecated)
                    _sanitize_allowlist_path = deprecated
                    break
        self.sanitize_allowlist_file = _sanitize_allowlist_path

        self.allowed_origin_hostnames = self._parse_allowed_origin_hostnames(self.allowed_origin_hostnames)
        if "trust_jupyter_notebook_conversion" not in kwargs:
            # if option not set, check IPython-named alternative, falling back to schema default if not set either
            _default = self.trust_jupyter_notebook_conversion
            self.trust_jupyter_notebook_conversion = string_as_bool(kwargs.get('trust_ipython_notebook_conversion', _default))
        # Configuration for the message box directly below the masthead.
        self.blog_url = kwargs.get('blog_url')
        self.user_library_import_symlink_allowlist = listify(self.user_library_import_symlink_allowlist, do_strip=True)
        self.user_library_import_dir_auto_creation = self.user_library_import_dir_auto_creation if self.user_library_import_dir else False
        # Searching data libraries
        self.ftp_upload_dir_template = kwargs.get('ftp_upload_dir_template', '${ftp_upload_dir}%s${ftp_upload_dir_identifier}' % os.path.sep)
        # Support older library-specific path paste option but just default to the new
        # allow_path_paste value.
        self.allow_library_path_paste = string_as_bool(kwargs.get('allow_library_path_paste', self.allow_path_paste))
        self.disable_library_comptypes = kwargs.get('disable_library_comptypes', '').lower().split(',')
        self.check_upload_content = string_as_bool(kwargs.get('check_upload_content', True))
        # On can mildly speed up Galaxy startup time by disabling index of help,
        # not needed on production systems but useful if running many functional tests.
        self.index_tool_help = string_as_bool(kwargs.get("index_tool_help", True))
        self.tool_labels_boost = kwargs.get("tool_labels_boost", 1)
        default_tool_test_data_directories = os.environ.get("GALAXY_TEST_FILE_DIR", self._in_root_dir("test-data"))
        self.tool_test_data_directories = kwargs.get("tool_test_data_directories", default_tool_test_data_directories)
        # Deployers may either specify a complete list of mapping files or get the default for free and just
        # specify a local mapping file to adapt and extend the default one.
        if "conda_mapping_files" not in kwargs:
            _default_mapping = self._in_root_dir(os.path.join("lib", "galaxy", "tool_util", "deps", "resolvers", "default_conda_mapping.yml"))
            # dependency resolution options are consumed via config_dict - so don't populate
            # self, populate config_dict
            self.config_dict["conda_mapping_files"] = [self.local_conda_mapping_file, _default_mapping]

        if self.container_resolvers_config_file:
            self.container_resolvers_config_file = self._in_config_dir(self.container_resolvers_config_file)

        # tool_dependency_dir can be "none" (in old configs). If so, set it to None
        if self.tool_dependency_dir and self.tool_dependency_dir.lower() == 'none':
            self.tool_dependency_dir = None
        if self.involucro_path is None:
            target_dir = self.tool_dependency_dir or self.schema.defaults['tool_dependency_dir']
            self.involucro_path = self._in_data_dir(os.path.join(target_dir, "involucro"))
        self.involucro_path = self._in_root_dir(self.involucro_path)
        if self.mulled_channels:
            self.mulled_channels = [c.strip() for c in self.mulled_channels.split(',')]

        default_job_resubmission_condition = kwargs.get('default_job_resubmission_condition', '')
        if not default_job_resubmission_condition.strip():
            default_job_resubmission_condition = None
        self.default_job_resubmission_condition = default_job_resubmission_condition

        # Configuration options for taking advantage of nginx features
        if self.nginx_upload_store:
            self.nginx_upload_store = os.path.abspath(self.nginx_upload_store)

        self.object_store = kwargs.get('object_store', 'disk')
        self.object_store_check_old_style = string_as_bool(kwargs.get('object_store_check_old_style', False))
        self.object_store_cache_path = self._in_root_dir(kwargs.get("object_store_cache_path", self._in_data_dir("object_store_cache")))
        self._configure_dataset_storage()

        # Handle AWS-specific config options for backward compatibility
        if kwargs.get('aws_access_key') is not None:
            self.os_access_key = kwargs.get('aws_access_key')
            self.os_secret_key = kwargs.get('aws_secret_key')
            self.os_bucket_name = kwargs.get('s3_bucket')
            self.os_use_reduced_redundancy = kwargs.get('use_reduced_redundancy', False)
        else:
            self.os_access_key = kwargs.get('os_access_key')
            self.os_secret_key = kwargs.get('os_secret_key')
            self.os_bucket_name = kwargs.get('os_bucket_name')
            self.os_use_reduced_redundancy = kwargs.get('os_use_reduced_redundancy', False)
        self.os_host = kwargs.get('os_host')
        self.os_port = kwargs.get('os_port')
        self.os_is_secure = string_as_bool(kwargs.get('os_is_secure', True))
        self.os_conn_path = kwargs.get('os_conn_path', '/')
        self.object_store_cache_size = float(kwargs.get('object_store_cache_size', -1))
        self.distributed_object_store_config_file = kwargs.get('distributed_object_store_config_file')
        if self.distributed_object_store_config_file is not None:
            self.distributed_object_store_config_file = self._in_root_dir(self.distributed_object_store_config_file)
        self.irods_root_collection_path = kwargs.get('irods_root_collection_path')
        self.irods_default_resource = kwargs.get('irods_default_resource')
        # Heartbeat log file name override
        if self.global_conf is not None and 'heartbeat_log' in self.global_conf:
            self.heartbeat_log = self.global_conf['heartbeat_log']
        # Determine which 'server:' this is
        self.server_name = 'main'
        for arg in sys.argv:
            # Crummy, but PasteScript does not give you a way to determine this
            if arg.lower().startswith('--server-name='):
                self.server_name = arg.split('=', 1)[-1]
        # Allow explicit override of server name in config params
        if "server_name" in kwargs:
            self.server_name = kwargs.get("server_name")
        # The application stack code may manipulate the server name. It also needs to be accessible via the get() method
        # for galaxy.util.facts()
        self.config_dict['base_server_name'] = self.base_server_name = self.server_name
        # Store all configured server names for the message queue routing
        self.server_names = []
        for section in self.global_conf_parser.sections():
            if section.startswith('server:'):
                self.server_names.append(section.replace('server:', '', 1))

        self._set_galaxy_infrastructure_url(kwargs)

        # Asynchronous execution process pools - limited functionality for now, attach_to_pools is designed to allow
        # webless Galaxy server processes to attach to arbitrary message queues (e.g. as job handlers) so they do not
        # have to be explicitly defined as such in the job configuration.
        self.attach_to_pools = kwargs.get('attach_to_pools', []) or []

        # Store advanced job management config
        self.job_handlers = [x.strip() for x in kwargs.get('job_handlers', self.server_name).split(',')]
        self.default_job_handlers = [x.strip() for x in kwargs.get('default_job_handlers', ','.join(self.job_handlers)).split(',')]
        # Galaxy internal control queue configuration.
        # If specified in universe, use it, otherwise we use whatever 'real'
        # database is specified.  Lastly, we create and use new sqlite database
        # (to minimize locking) as a final option.
        if 'amqp_internal_connection' in kwargs:
            self.amqp_internal_connection = kwargs.get('amqp_internal_connection')
            # TODO Get extra amqp args as necessary for ssl
        elif 'database_connection' in kwargs:
            self.amqp_internal_connection = f"sqlalchemy+{self.database_connection}"
        else:
            self.amqp_internal_connection = f"sqlalchemy+sqlite:///{self._in_data_dir('control.sqlite')}?isolation_level=IMMEDIATE"
        self.pretty_datetime_format = expand_pretty_datetime_format(self.pretty_datetime_format)
        try:
            with open(self.user_preferences_extra_conf_path) as stream:
                self.user_preferences_extra = yaml.safe_load(stream)
        except Exception:
            if self.is_set('user_preferences_extra_conf_path'):
                log.warning(f'Config file ({self.user_preferences_extra_conf_path}) could not be found or is malformed.')
            self.user_preferences_extra = {'preferences': {}}

        # Experimental: This will not be enabled by default and will hide
        # nonproduction code.
        # The api_folders refers to whether the API exposes the /folders section.
        self.api_folders = string_as_bool(kwargs.get('api_folders', False))
        # This is for testing new library browsing capabilities.
        self.new_lib_browse = string_as_bool(kwargs.get('new_lib_browse', False))
        # Logging configuration with logging.config.configDict:
        # Statistics and profiling with statsd
        self.statsd_host = kwargs.get('statsd_host', '')

        ie_dirs = self.interactive_environment_plugins_directory
        self.gie_dirs = [d.strip() for d in (ie_dirs.split(",") if ie_dirs else [])]
        if ie_dirs:
            self.visualization_plugins_directory += f",{ie_dirs}"

        self.proxy_session_map = self.dynamic_proxy_session_map
        self.manage_dynamic_proxy = self.dynamic_proxy_manage  # Set to false if being launched externally

        # InteractiveTools propagator mapping file
        self.interactivetools_map = self._in_root_dir(kwargs.get("interactivetools_map", self._in_data_dir("interactivetools_map.sqlite")))

        self.containers_conf = parse_containers_config(self.containers_config_file)

        # Compliance/Policy variables
        self.redact_username_during_deletion = False
        self.redact_email_during_deletion = False
        self.redact_ip_address = False
        self.redact_username_in_logs = False
        self.redact_email_in_job_name = False
        self.redact_user_details_in_bugreport = False
        self.redact_user_address_during_deletion = False
        # GDPR compliance mode changes values on a number of variables. Other
        # policies could change (non)overlapping subsets of these variables.
        if self.enable_beta_gdpr:
            self.expose_user_name = False
            self.expose_user_email = False

            self.redact_username_during_deletion = True
            self.redact_email_during_deletion = True
            self.redact_ip_address = True
            self.redact_username_in_logs = True
            self.redact_email_in_job_name = True
            self.redact_user_details_in_bugreport = True
            self.redact_user_address_during_deletion = True
            self.allow_user_deletion = True

            LOGGING_CONFIG_DEFAULT['formatters']['brief'] = {
                'format': '%(asctime)s %(levelname)-8s %(name)-15s %(message)s'
            }
            LOGGING_CONFIG_DEFAULT['handlers']['compliance_log'] = {
                'class': 'logging.handlers.RotatingFileHandler',
                'formatter': 'brief',
                'filename': 'compliance.log',
                'backupCount': 0,
            }
            LOGGING_CONFIG_DEFAULT['loggers']['COMPLIANCE'] = {
                'handlers': ['compliance_log'],
                'level': 'DEBUG',
                'qualname': 'COMPLIANCE'
            }

        log_destination = kwargs.get("log_destination")
        galaxy_daemon_log_destination = os.environ.get('GALAXY_DAEMON_LOG')
        if log_destination == "stdout":
            LOGGING_CONFIG_DEFAULT['handlers']['console'] = {
                'class': 'logging.StreamHandler',
                'formatter': 'stack',
                'level': 'DEBUG',
                'stream': 'ext://sys.stdout',
                'filters': ['stack']
            }
        elif log_destination:
            LOGGING_CONFIG_DEFAULT['handlers']['console'] = {
                'class': 'logging.FileHandler',
                'formatter': 'stack',
                'level': 'DEBUG',
                'filename': log_destination,
                'filters': ['stack']
            }
        if galaxy_daemon_log_destination:
            LOGGING_CONFIG_DEFAULT['handlers']['files'] = {
                'class': 'logging.FileHandler',
                'formatter': 'stack',
                'level': 'DEBUG',
                'filename': galaxy_daemon_log_destination,
                'filters': ['stack']
            }
            LOGGING_CONFIG_DEFAULT['root']['handlers'].append('files')

    def _configure_dataset_storage(self):
        # The default for `file_path` has changed in 20.05; we may need to fall back to the old default
        self._set_alt_paths('file_path', self._in_data_dir('files'))  # this is called BEFORE guessing id/uuid
        ID, UUID = 'id', 'uuid'
        if self.is_set('object_store_store_by'):
            assert self.object_store_store_by in [ID, UUID], f"Invalid value for object_store_store_by [{self.object_store_store_by}]"
        elif os.path.basename(self.file_path) == 'objects':
            self.object_store_store_by = UUID
        else:
            self.object_store_store_by = ID

    def _load_list_from_file(self, filepath):
        with open(filepath) as f:
            return [line.strip() for line in f]

    def _set_galaxy_infrastructure_url(self, kwargs):
        # indicate if this was not set explicitly, so dependending on the context a better default
        # can be used (request url in a web thread, Docker parent in IE stuff, etc.)
        self.galaxy_infrastructure_url_set = kwargs.get('galaxy_infrastructure_url') is not None
        if "HOST_IP" in self.galaxy_infrastructure_url:
            self.galaxy_infrastructure_url = string.Template(self.galaxy_infrastructure_url).safe_substitute({
                'HOST_IP': socket.gethostbyname(socket.gethostname())
            })
        if "GALAXY_WEB_PORT" in self.galaxy_infrastructure_url:
            port = os.environ.get('GALAXY_WEB_PORT')
            if not port:
                raise Exception('$GALAXY_WEB_PORT set in galaxy_infrastructure_url, but environment variable not set')
            self.galaxy_infrastructure_url = string.Template(self.galaxy_infrastructure_url).safe_substitute({
                'GALAXY_WEB_PORT': port
            })
        if "UWSGI_PORT" in self.galaxy_infrastructure_url:
            import uwsgi
            http = unicodify(uwsgi.opt['http'])
            host, port = http.split(":", 1)
            assert port, "galaxy_infrastructure_url depends on dynamic PORT determination but port unknown"
            self.galaxy_infrastructure_url = string.Template(self.galaxy_infrastructure_url).safe_substitute({
                'UWSGI_PORT': port
            })

    def reload_sanitize_allowlist(self, explicit=True):
        self.sanitize_allowlist = []
        try:
            with open(self.sanitize_allowlist_file) as f:
                for line in f.readlines():
                    if not line.startswith("#"):
                        self.sanitize_allowlist.append(line.strip())
        except OSError:
            if explicit:
                log.warning("Sanitize log file explicitly specified as '%s' but does not exist, continuing with no tools allowlisted.", self.sanitize_allowlist_file)

    def ensure_tempdir(self):
        self._ensure_directory(self.new_file_path)

    def check(self):
        # Check that required directories exist; attempt to create otherwise
        paths_to_check = [
            self.data_dir,
            self.ftp_upload_dir,
            self.library_import_dir,
            self.managed_config_dir,
            self.new_file_path,
            self.nginx_upload_store,
            self.object_store_cache_path,
            self.template_cache_path,
            self.tool_data_path,
            self.user_library_import_dir,
        ]
        for path in paths_to_check:
            self._ensure_directory(path)
        # Check that required files exist
        tool_configs = self.tool_configs
        for path in tool_configs:
            if not os.path.exists(path) and path not in (self.shed_tool_config_file, self.migrated_tools_config):
                raise ConfigurationError(f"Tool config file not found: {path}")
        for datatypes_config in listify(self.datatypes_config):
            if not os.path.isfile(datatypes_config):
                raise ConfigurationError(f"Datatypes config file not found: {datatypes_config}")
        # Check for deprecated options.
        for key in self.config_dict.keys():
            if key in self.deprecated_options:
                log.warning(f"Config option '{key}' is deprecated and will be removed in a future release.  Please consult the latest version of the sample configuration file.")

    @staticmethod
    def _parse_allowed_origin_hostnames(allowed_origin_hostnames):
        """
        Parse a CSV list of strings/regexp of hostnames that should be allowed
        to use CORS and will be sent the Access-Control-Allow-Origin header.
        """
        allowed_origin_hostnames_list = listify(allowed_origin_hostnames)
        if not allowed_origin_hostnames_list:
            return None

        def parse(string):
            # a string enclosed in fwd slashes will be parsed as a regexp: e.g. /<some val>/
            if string[0] == '/' and string[-1] == '/':
                string = string[1:-1]
                return re.compile(string, flags=(re.UNICODE))
            return string

        return [parse(v) for v in allowed_origin_hostnames_list if v]


# legacy naming
Configuration = GalaxyAppConfiguration


def reload_config_options(current_config):
    """Reload modified reloadable config options."""
    modified_config = read_properties_from_file(current_config.config_file)
    for option in current_config.schema.reloadable_options:
        if option in modified_config:
            # compare to raw value, as that one is set only on load and reload
            if current_config._raw_config[option] != modified_config[option]:
                current_config._raw_config[option] = modified_config[option]
                setattr(current_config, option, modified_config[option])
                log.info(f'Reloaded {option}')


def get_database_engine_options(kwargs, model_prefix=''):
    """
    Allow options for the SQLAlchemy database engine to be passed by using
    the prefix "database_engine_option".
    """
    conversions = {
        'convert_unicode': string_as_bool,
        'pool_timeout': int,
        'echo': string_as_bool,
        'echo_pool': string_as_bool,
        'pool_recycle': int,
        'pool_size': int,
        'max_overflow': int,
        'pool_threadlocal': string_as_bool,
        'server_side_cursors': string_as_bool
    }
    prefix = f"{model_prefix}database_engine_option_"
    prefix_len = len(prefix)
    rval = {}
    for key, value in kwargs.items():
        if key.startswith(prefix):
            key = key[prefix_len:]
            if key in conversions:
                value = conversions[key](value)
            rval[key] = value
    return rval


def get_database_url(config):
    db_url = config.database_connection
    return db_url


def init_models_from_config(config, map_install_models=False, object_store=None, trace_logger=None):
    db_url = get_database_url(config)
    model = mapping.init(
        config.file_path,
        db_url,
        config.database_engine_options,
        map_install_models=map_install_models,
        database_query_profiling_proxy=config.database_query_profiling_proxy,
        object_store=object_store,
        trace_logger=trace_logger,
        use_pbkdf2=config.get_bool('use_pbkdf2', True),
        slow_query_log_threshold=config.slow_query_log_threshold,
        thread_local_log=config.thread_local_log,
        log_query_counts=config.database_log_query_counts,
    )
    return model


def configure_logging(config):
    """Allow some basic logging configuration to be read from ini file.

    This should be able to consume either a galaxy.config.Configuration object
    or a simple dictionary of configuration variables.
    """
    # Get root logger
    logging.addLevelName(LOGLV_TRACE, "TRACE")
    root = logging.getLogger()
    # PasteScript will have already configured the logger if the
    # 'loggers' section was found in the config file, otherwise we do
    # some simple setup using the 'log_*' values from the config.
    parser = getattr(config, "global_conf_parser", None)
    if parser:
        paste_configures_logging = config.global_conf_parser.has_section("loggers")
    else:
        paste_configures_logging = False
    auto_configure_logging = not paste_configures_logging and string_as_bool(config.get("auto_configure_logging", "True"))
    if auto_configure_logging:
        logging_conf = config.get('logging', None)
        if logging_conf is None:
            # if using the default logging config, honor the log_level setting
            logging_conf = LOGGING_CONFIG_DEFAULT
            if config.get('log_level', 'DEBUG') != 'DEBUG':
                logging_conf['handlers']['console']['level'] = config.get('log_level', 'DEBUG')
        # configure logging with logging dict in config, template *FileHandler handler filenames with the `filename_template` option
        for name, conf in logging_conf.get('handlers', {}).items():
            if conf['class'].startswith('logging.') and conf['class'].endswith('FileHandler') and 'filename_template' in conf:
                conf['filename'] = conf.pop('filename_template').format(**get_stack_facts(config=config))
                logging_conf['handlers'][name] = conf
        logging.config.dictConfig(logging_conf)
    if getattr(config, "sentry_dsn", None):
        from raven.handlers.logging import SentryHandler
        sentry_handler = SentryHandler(config.sentry_dsn)
        sentry_handler.setLevel(logging.WARN)
        register_postfork_function(root.addHandler, sentry_handler)


class ConfiguresGalaxyMixin:
    """Shared code for configuring Galaxy-like app objects."""

    def _configure_genome_builds(self, data_table_name="__dbkeys__", load_old_style=True):
        self.genome_builds = GenomeBuilds(self, data_table_name=data_table_name, load_old_style=load_old_style)

    def wait_for_toolbox_reload(self, old_toolbox):
        timer = ExecutionTimer()
        log.debug('Waiting for toolbox reload')
        # Wait till toolbox reload has been triggered (or more than 60 seconds have passed)
        while timer.elapsed < 60:
            if self.toolbox.has_reloaded(old_toolbox):
                log.debug('Finished waiting for toolbox reload %s', timer)
                break
            time.sleep(0.1)
        else:
            log.warning('Waiting for toolbox reload timed out after 60 seconds')

    def _configure_tool_config_files(self):
        if self.config.shed_tool_config_file not in self.config.tool_configs:
            self.config.tool_configs.append(self.config.shed_tool_config_file)
        # The value of migrated_tools_config is the file reserved for containing only those tools that have been
        # eliminated from the distribution and moved to the tool shed. If migration checking is disabled, only add it if
        # it exists (since this may be an existing deployment where migrations were previously run).
        if ((self.config.check_migrate_tools or os.path.exists(self.config.migrated_tools_config))
                and self.config.migrated_tools_config not in self.config.tool_configs):
            self.config.tool_configs.append(self.config.migrated_tools_config)

    def _configure_toolbox(self):
        from galaxy import tools
        from galaxy.managers.citations import CitationsManager
        from galaxy.tool_util.deps import containers
        from galaxy.tool_util.deps.dependencies import AppInfo
        import galaxy.tools.search

        self.citations_manager = CitationsManager(self)

        from galaxy.managers.tools import DynamicToolManager
        self.dynamic_tools_manager = DynamicToolManager(self)
        self._toolbox_lock = threading.RLock()
        self.toolbox = tools.ToolBox(self.config.tool_configs, self.config.tool_path, self)
        galaxy_root_dir = os.path.abspath(self.config.root)
        file_path = os.path.abspath(self.config.file_path)
        app_info = AppInfo(
            galaxy_root_dir=galaxy_root_dir,
            default_file_path=file_path,
            tool_data_path=self.config.tool_data_path,
            shed_tool_data_path=self.config.shed_tool_data_path,
            outputs_to_working_directory=self.config.outputs_to_working_directory,
            container_image_cache_path=self.config.container_image_cache_path,
            library_import_dir=self.config.library_import_dir,
            enable_mulled_containers=self.config.enable_mulled_containers,
            container_resolvers_config_file=self.config.container_resolvers_config_file,
            container_resolvers_config_dict=self.config.container_resolvers,
            involucro_path=self.config.involucro_path,
            involucro_auto_init=self.config.involucro_auto_init,
            mulled_channels=self.config.mulled_channels,
        )
        mulled_resolution_cache = None
        if self.config.mulled_resolution_cache_type:
            cache_opts = {
                'cache.type': self.config.mulled_resolution_cache_type,
                'cache.data_dir': self.config.mulled_resolution_cache_data_dir,
                'cache.lock_dir': self.config.mulled_resolution_cache_lock_dir,
            }
            mulled_resolution_cache = CacheManager(**parse_cache_config_options(cache_opts)).get_cache('mulled_resolution')
        self.container_finder = containers.ContainerFinder(app_info, mulled_resolution_cache=mulled_resolution_cache)
        self._set_enabled_container_types()
        index_help = getattr(self.config, "index_tool_help", True)
        self.toolbox_search = galaxy.tools.search.ToolBoxSearch(self.toolbox, index_dir=self.config.tool_search_index_dir, index_help=index_help)

    def reindex_tool_search(self):
        # Call this when tools are added or removed.
        self.toolbox_search.build_index(tool_cache=self.tool_cache)
        self.tool_cache.reset_status()

    def _set_enabled_container_types(self):
        container_types_to_destinations = collections.defaultdict(list)
        for destinations in self.job_config.destinations.values():
            for destination in destinations:
                for enabled_container_type in self.container_finder._enabled_container_types(destination.params):
                    container_types_to_destinations[enabled_container_type].append(destination)
        self.toolbox.dependency_manager.set_enabled_container_types(container_types_to_destinations)
        self.toolbox.dependency_manager.resolver_classes.update(self.container_finder.default_container_registry.resolver_classes)
        self.toolbox.dependency_manager.dependency_resolvers.extend(self.container_finder.default_container_registry.container_resolvers)

    def _configure_tool_data_tables(self, from_shed_config):
        from galaxy.tools.data import ToolDataTableManager

        # Initialize tool data tables using the config defined by self.config.tool_data_table_config_path.
        self.tool_data_tables = ToolDataTableManager(tool_data_path=self.config.tool_data_path,
                                                     config_filename=self.config.tool_data_table_config_path,
                                                     other_config_dict=self.config)
        # Load additional entries defined by self.config.shed_tool_data_table_config into tool data tables.
        try:
            self.tool_data_tables.load_from_config_file(config_filename=self.config.shed_tool_data_table_config,
                                                        tool_data_path=self.tool_data_tables.tool_data_path,
                                                        from_shed_config=from_shed_config)
        except OSError as exc:
            # Missing shed_tool_data_table_config is okay if it's the default
            if exc.errno != errno.ENOENT or self.config.is_set('shed_tool_data_table_config'):
                raise

    def _configure_datatypes_registry(self, installed_repository_manager=None):
        from galaxy.datatypes import registry
        # Create an empty datatypes registry.
        self.datatypes_registry = registry.Registry(self.config)
        if installed_repository_manager:
            # Load proprietary datatypes defined in datatypes_conf.xml files in all installed tool shed repositories.  We
            # load proprietary datatypes before datatypes in the distribution because Galaxy's default sniffers include some
            # generic sniffers (eg text,xml) which catch anything, so it's impossible for proprietary sniffers to be used.
            # However, if there is a conflict (2 datatypes with the same extension) between a proprietary datatype and a datatype
            # in the Galaxy distribution, the datatype in the Galaxy distribution will take precedence.  If there is a conflict
            # between 2 proprietary datatypes, the datatype from the repository that was installed earliest will take precedence.
            installed_repository_manager.load_proprietary_datatypes()
        # Load the data types in the Galaxy distribution, which are defined in self.config.datatypes_config.
        datatypes_configs = self.config.datatypes_config
        for datatypes_config in listify(datatypes_configs):
            # Setting override=False would make earlier files would take
            # precedence - but then they wouldn't override tool shed
            # datatypes.
            self.datatypes_registry.load_datatypes(self.config.root, datatypes_config, override=True)

    def _configure_object_store(self, **kwds):
        from galaxy.objectstore import build_object_store_from_config
        self.object_store = build_object_store_from_config(self.config, **kwds)

    def _configure_security(self):
        from galaxy.security import idencoding
        self.security = idencoding.IdEncodingHelper(id_secret=self.config.id_secret)

    def _configure_tool_shed_registry(self):
        import galaxy.tool_shed.tool_shed_registry

        # Set up the tool sheds registry
        if os.path.isfile(self.config.tool_sheds_config_file):
            self.tool_shed_registry = galaxy.tool_shed.tool_shed_registry.Registry(self.config.tool_sheds_config_file)
        else:
            self.tool_shed_registry = galaxy.tool_shed.tool_shed_registry.Registry()

    def _configure_models(self, check_migrate_databases=False, check_migrate_tools=False, config_file=None):
        """Preconditions: object_store must be set on self."""
        db_url = get_database_url(self.config)
        install_db_url = self.config.install_database_connection
        # TODO: Consider more aggressive check here that this is not the same
        # database file under the hood.
        combined_install_database = not(install_db_url and install_db_url != db_url)
        install_db_url = install_db_url or db_url
        install_database_options = self.config.database_engine_options if combined_install_database else self.config.install_database_engine_options

        if self.config.database_wait:
            self._wait_for_database(db_url)

        if getattr(self.config, "max_metadata_value_size", None):
            from galaxy.model import custom_types
            custom_types.MAX_METADATA_VALUE_SIZE = self.config.max_metadata_value_size

        if check_migrate_databases:
            # Initialize database / check for appropriate schema version.  # If this
            # is a new installation, we'll restrict the tool migration messaging.
            from galaxy.model.migrate.check import create_or_verify_database
            create_or_verify_database(db_url, config_file, self.config.database_engine_options, app=self, map_install_models=combined_install_database)
            if not combined_install_database:
                tsi_create_or_verify_database(install_db_url, install_database_options, app=self)

        if check_migrate_tools:
            # Alert the Galaxy admin to tools that have been moved from the distribution to the tool shed.
            from galaxy.tool_shed.galaxy_install.migrate.check import verify_tools
            verify_tools(self, install_db_url, config_file, install_database_options)

        self.model = init_models_from_config(
            self.config,
            map_install_models=combined_install_database,
            object_store=self.object_store,
            trace_logger=getattr(self, "trace_logger", None)
        )
        if combined_install_database:
            log.info("Install database targetting Galaxy's database configuration.")
            self.install_model = self.model
        else:
            from galaxy.model.tool_shed_install import mapping as install_mapping
            install_db_url = self.config.install_database_connection
            log.info(f"Install database using its own connection {install_db_url}")
            self.install_model = install_mapping.init(install_db_url,
                                                      install_database_options)

    def _configure_signal_handlers(self, handlers):
        for sig, handler in handlers.items():
            signal.signal(sig, handler)

    def _wait_for_database(self, url):
        attempts = self.config.database_wait_attempts
        pause = self.config.database_wait_sleep
        for i in range(1, attempts):
            try:
                database_exists(url)
                break
            except Exception:
                log.info("Waiting for database: attempt %d of %d" % (i, attempts))
                time.sleep(pause)

    @property
    def tool_dependency_dir(self):
        return self.toolbox.dependency_manager.default_base_path
