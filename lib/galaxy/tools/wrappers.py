import logging
import os
import shlex
import tempfile
from functools import total_ordering

from galaxy import exceptions
from galaxy.model.none_like import NoneDataset
from galaxy.tools.parameters.wrapped_json import (
    data_collection_input_to_staging_path_and_source_path,
    data_input_to_staging_path_and_source_path,
)
from galaxy.util import filesystem_safe_string
from galaxy.util.object_wrapper import wrap_with_safe_string

log = logging.getLogger(__name__)

# Fields in .log files corresponding to paths, must have one of the following
# field names and all such fields are assumed to be paths. This is to allow
# remote ComputeEnvironments (such as one used by Pulsar) determine what values to
# rewrite or transfer...
PATH_ATTRIBUTES = ["path"]


class ToolParameterValueWrapper:
    """
    Base class for object that Wraps a Tool Parameter and Value.
    """

    def __bool__(self):
        return bool(self.value)
    __nonzero__ = __bool__

    def get_display_text(self, quote=True):
        """
        Returns a string containing the value that would be displayed to the user in the tool interface.
        When quote is True (default), the string is escaped for e.g. command-line usage.
        """
        rval = self.input.value_to_display_text(self.value) or ''
        if quote:
            return shlex.quote(rval)
        return rval


class RawObjectWrapper(ToolParameterValueWrapper):
    """
    Wraps an object so that __str__ returns module_name:class_name.
    """

    def __init__(self, obj):
        self.obj = obj

    def __bool__(self):
        return bool(self.obj)  # FIXME: would it be safe/backwards compatible to rename .obj to .value, so that we can just inherit this method?
    __nonzero__ = __bool__

    def __str__(self):
        try:
            return f"{self.obj.__module__}:{self.obj.__class__.__name__}"
        except Exception:
            # Most likely None, which lacks __module__.
            return str(self.obj)

    def __getattr__(self, key):
        return getattr(self.obj, key)


@total_ordering
class InputValueWrapper(ToolParameterValueWrapper):
    """
    Wraps an input so that __str__ gives the "param_dict" representation.
    """

    def __init__(self, input, value, other_values=None):
        self.input = input
        self.value = value
        self._other_values = other_values or {}

    def _get_cast_value(self, other):
        if self.input.type == 'boolean' and isinstance(other, str):
            return str(self)
        # For backward compatibility, allow `$wrapper != ""` for optional non-text param
        if self.input.optional and self.value is None:
            if isinstance(other, str):
                return str(self)
            else:
                return None
        cast = {
            'text': str,
            'integer': int,
            'float': float,
            'boolean': bool,
        }
        return cast.get(self.input.type, str)(self)

    def __eq__(self, other):
        return self._get_cast_value(other) == other

    def __ne__(self, other):
        return not self == other

    def __str__(self):
        to_param_dict_string = self.input.to_param_dict_string(self.value, self._other_values)
        if isinstance(to_param_dict_string, list):
            return ','.join(to_param_dict_string)
        else:
            return to_param_dict_string

    def __iter__(self):
        to_param_dict_string = self.input.to_param_dict_string(self.value, self._other_values)
        if not isinstance(to_param_dict_string, list):
            return iter([to_param_dict_string])
        else:
            return iter(to_param_dict_string)

    def __getattr__(self, key):
        return getattr(self.value, key)

    def __gt__(self, other):
        return self._get_cast_value(other) > other

    def __int__(self):
        return int(float(self))

    def __float__(self):
        return float(str(self))


class SelectToolParameterWrapper(ToolParameterValueWrapper):
    """
    Wraps a SelectTooParameter so that __str__ returns the selected value, but all other
    attributes are accessible.
    """

    class SelectToolParameterFieldWrapper:
        """
        Provide access to any field by name or index for this particular value.
        Only applicable for dynamic_options selects, which have more than simple 'options' defined (name, value, selected).
        """

        def __init__(self, input, value, other_values, compute_environment):
            self._input = input
            self._value = value
            self._other_values = other_values
            self._fields = {}
            self._compute_environment = compute_environment

        def __getattr__(self, name):
            if name not in self._fields:
                self._fields[name] = self._input.options.get_field_by_name_for_value(name, self._value, None, self._other_values)
            values = map(str, self._fields[name])
            if name in PATH_ATTRIBUTES and self._compute_environment:
                # If we infer this is a path, rewrite it if needed.
                new_values = []
                for value in values:
                    rewrite_value = self._compute_environment.unstructured_path_rewrite(value)
                    if rewrite_value:
                        new_values.append(rewrite_value)
                    else:
                        new_values.append(value)

                values = new_values

            return self._input.separator.join(values)

    def __init__(self, input, value, other_values=None, compute_environment=None):
        self.input = input
        self.value = value
        self.input.value_label = input.value_to_display_text(value)
        self._other_values = other_values or {}
        self.compute_environment = compute_environment
        self.fields = self.SelectToolParameterFieldWrapper(input, value, other_values, self.compute_environment)

    def __eq__(self, other):
        if isinstance(other, str):
            if other == '' and self.value in (None, []):
                # Allow $wrapper == '' for select (self.value is None) and multiple select (self.value is []) params
                return True
            return str(self) == other
        else:
            return super() == other

    def __ne__(self, other):
        return not self == other

    def __str__(self):
        # Assuming value is never a path - otherwise would need to pass
        # along following argument value_map=self._path_rewriter.
        return self.input.to_param_dict_string(self.value, other_values=self._other_values)

    def __add__(self, x):
        return f'{self}{x}'

    def __getattr__(self, key):
        return getattr(self.input, key)

    def __iter__(self):
        if not self.input.multiple:
            raise Exception("Tried to iterate over a non-multiple parameter.")
        return self.value.__iter__()


class DatasetFilenameWrapper(ToolParameterValueWrapper):
    """
    Wraps a dataset so that __str__ returns the filename, but all other
    attributes are accessible.
    """

    class MetadataWrapper:
        """
        Wraps a Metadata Collection to return MetadataParameters wrapped
        according to the metadata spec. Methods implemented to match behavior
        of a Metadata Collection.
        """

        def __init__(self, dataset, compute_environment=None):
            self.dataset = dataset
            self.metadata = dataset.metadata
            self.compute_environment = compute_environment

        def __getattr__(self, name):
            rval = self.metadata.get(name, None)
            if name in self.metadata.spec:
                if rval is None:
                    rval = self.metadata.spec[name].no_value
                metadata_param = self.metadata.spec[name].param
                from galaxy.model.metadata import FileParameter
                rval = metadata_param.to_safe_string(rval)
                if isinstance(metadata_param, FileParameter) and self.compute_environment:
                    rewrite = self.compute_environment.input_metadata_rewrite(self.dataset, rval)
                    if rewrite is not None:
                        rval = rewrite

                # Store this value, so we don't need to recalculate if needed
                # again
                setattr(self, name, rval)
            else:
                # escape string value of non-defined metadata value
                rval = wrap_with_safe_string(rval)
            return rval

        def __bool__(self):
            return self.metadata.__nonzero__()
        __nonzero__ = __bool__

        def __iter__(self):
            return self.metadata.__iter__()

        def element_is_set(self, name):
            return self.metadata.element_is_set(name)

        def get(self, key, default=None):
            try:
                return getattr(self, key)
            except Exception:
                return default

        def items(self):
            return iter((k, self.get(k)) for k, v in self.metadata.items())

    def __init__(self, dataset, datatypes_registry=None, tool=None, name=None, compute_environment=None, identifier=None, io_type="input", formats=None):
        if not dataset:
            try:
                # TODO: allow this to work when working with grouping
                ext = tool.inputs[name].extensions[0]
            except Exception:
                ext = 'data'
            self.dataset = wrap_with_safe_string(NoneDataset(datatypes_registry=datatypes_registry, ext=ext), no_wrap_classes=ToolParameterValueWrapper)
        else:
            # Tool wrappers should not normally be accessing .dataset directly,
            # so we will wrap it and keep the original around for file paths
            # Should we name this .value to maintain consistency with most other ToolParameterValueWrapper?
            if formats:
                direct_match, target_ext, converted_dataset = dataset.find_conversion_destination(formats)
                if not direct_match and target_ext and converted_dataset:
                    dataset = converted_dataset
            self.unsanitized = dataset
            self.dataset = wrap_with_safe_string(dataset, no_wrap_classes=ToolParameterValueWrapper)
            self.metadata = self.MetadataWrapper(dataset, compute_environment)
            if hasattr(dataset, 'tags'):
                self.groups = {tag.user_value.lower() for tag in dataset.tags if tag.user_tname == 'group'}
            else:
                # May be a 'FakeDatasetAssociation'
                self.groups = set()
        self.compute_environment = compute_environment
        # TODO: lazy initialize this...
        self.__io_type = io_type
        if self.__io_type == "input":
            path_rewrite = compute_environment and dataset and compute_environment.input_path_rewrite(dataset)
            if path_rewrite:
                self.false_path = path_rewrite
            else:
                self.false_path = None
        else:
            path_rewrite = compute_environment and compute_environment.output_path_rewrite(dataset)
            if path_rewrite:
                self.false_path = path_rewrite
            else:
                self.false_path = None
        self.datatypes_registry = datatypes_registry
        self._element_identifier = identifier

    @property
    def element_identifier(self):
        identifier = self._element_identifier
        if identifier is None:
            identifier = self.name
        return identifier

    @property
    def file_ext(self):
        return getattr(self.unsanitized.datatype, 'file_ext_export_alias', self.dataset.extension)

    @property
    def name_and_ext(self):
        return f"{self.element_identifier}.{self.file_ext}"

    def get_staging_path(self, invalid_chars=('/',)):
        """
        Strip leading dots, unicode null chars, replace `/` with `_`, truncate at 255 characters.

        Not safe for commandline use, would need additional sanitization.
        """
        max_len = 254 - len(self.file_ext)
        safe_element_identifier = filesystem_safe_string(self.element_identifier, max_len=max_len, invalid_chars=invalid_chars)
        return f"{safe_element_identifier}.{self.file_ext}"

    @property
    def all_metadata_files(self):
        return self.unsanitized.get_metadata_file_paths_and_extensions() if self else []

    def serialize(self, invalid_chars=('/',)):
        return data_input_to_staging_path_and_source_path(self, invalid_chars=invalid_chars) if self else {}

    @property
    def is_collection(self):
        return False

    def is_of_type(self, *exts):
        datatypes = []
        for e in exts:
            datatype = self.datatypes_registry.get_datatype_by_extension(e)
            if datatype is not None:
                datatypes.append(datatype)
            else:
                log.warning(f"Datatype class not found for extension '{e}', which is used as parameter of 'is_of_type()' method")
        return self.dataset.datatype.matches_any(datatypes)

    def __str__(self):
        if self.false_path is not None:
            return self.false_path
        else:
            return self.unsanitized.file_name

    def __getattr__(self, key):
        if self.false_path is not None and key == 'file_name':
            # Path to dataset was rewritten for this job.
            return self.false_path
        elif key == 'extra_files_path':
            if self.__io_type == "input":
                path_rewrite = self.compute_environment and self.compute_environment.input_extra_files_rewrite(self.unsanitized)
            else:
                path_rewrite = self.compute_environment and self.compute_environment.output_extra_files_rewrite(self.unsanitized)
            if path_rewrite:
                return path_rewrite
            else:
                try:
                    # Assume it is an output and that this wrapper
                    # will be set with correct "files_path" for this
                    # job.
                    return self.files_path
                except AttributeError:
                    # Otherwise, we have an input - delegate to model and
                    # object store to find the static location of this
                    # directory.
                    try:
                        return self.unsanitized.extra_files_path
                    except exceptions.ObjectNotFound:
                        # NestedObjectstore raises an error here
                        # instead of just returning a non-existent
                        # path like DiskObjectStore.
                        raise
        elif key == 'serialize':
            return self.serialize
        else:
            return getattr(self.dataset, key)

    def __bool__(self):
        return bool(self.dataset)
    __nonzero__ = __bool__


class HasDatasets:

    def _dataset_wrapper(self, dataset, **kwargs):
        return DatasetFilenameWrapper(dataset, **kwargs)

    def paths_as_file(self, sep="\n"):
        contents = sep.join(map(str, self))
        with tempfile.NamedTemporaryFile(mode='w+', prefix="gx_file_list", dir=self.job_working_directory, delete=False) as fh:
            fh.write(contents)
            filepath = fh.name
        return filepath


class DatasetListWrapper(list, ToolParameterValueWrapper, HasDatasets):
    """
    """

    def __init__(self, job_working_directory, datasets, **kwargs):
        self._dataset_elements_cache = {}
        if not isinstance(datasets, list):
            datasets = [datasets]

        def to_wrapper(dataset):
            if hasattr(dataset, "dataset_instance"):
                element = dataset
                dataset = element.dataset_instance
                kwargs["identifier"] = element.element_identifier
            return self._dataset_wrapper(dataset, **kwargs)

        list.__init__(self, map(to_wrapper, datasets))
        self.job_working_directory = job_working_directory

    @staticmethod
    def to_dataset_instances(dataset_instance_sources):
        dataset_instances = []
        if not isinstance(dataset_instance_sources, list):
            dataset_instance_sources = [dataset_instance_sources]
        for dataset_instance_source in dataset_instance_sources:
            if dataset_instance_source is None:
                dataset_instances.append(dataset_instance_source)
            elif getattr(dataset_instance_source, "history_content_type", None) == "dataset":
                dataset_instances.append(dataset_instance_source)
            elif hasattr(dataset_instance_source, "child_collection"):
                dataset_instances.extend(dataset_instance_source.child_collection.dataset_elements)
            else:
                dataset_instances.extend(dataset_instance_source.collection.dataset_elements)
        return dataset_instances

    def get_datasets_for_group(self, group):
        group = str(group).lower()
        if not self._dataset_elements_cache.get(group):
            wrappers = []
            for element in self:
                if any([t for t in element.tags if t.user_tname.lower() == 'group' and t.value.lower() == group]):
                    wrappers.append(element)
            self._dataset_elements_cache[group] = wrappers
        return self._dataset_elements_cache[group]

    def serialize(self, invalid_chars=('/',)):
        return [v.serialize(invalid_chars) for v in self]

    def __str__(self):
        return ','.join(map(str, self))

    def __bool__(self):
        # Fail `#if $param` checks in cheetah if optional input is not provided
        return any(self)
    __nonzero__ = __bool__


class DatasetCollectionWrapper(ToolParameterValueWrapper, HasDatasets):

    def __init__(self, job_working_directory, has_collection, datatypes_registry=None, **kwargs):
        super().__init__()
        self.job_working_directory = job_working_directory
        self._dataset_elements_cache = {}
        self._element_identifiers_extensions_paths_and_metadata_files = None
        self.datatypes_registry = datatypes_registry
        self.kwargs = kwargs

        if has_collection is None:
            self.__input_supplied = False
            return
        else:
            self.__input_supplied = True

        if hasattr(has_collection, "name"):
            # It is a HistoryDatasetCollectionAssociation
            collection = has_collection.collection
            self.name = has_collection.name
        elif hasattr(has_collection, "child_collection"):
            # It is a DatasetCollectionElement instance referencing another collection
            collection = has_collection.child_collection
            self.name = has_collection.element_identifier
        else:
            collection = has_collection
            self.name = None
        self.collection = collection

        elements = collection.elements
        element_instances = {}

        element_instance_list = []
        for dataset_collection_element in elements:
            element_object = dataset_collection_element.element_object
            element_identifier = dataset_collection_element.element_identifier

            if dataset_collection_element.is_collection:
                element_wrapper = DatasetCollectionWrapper(job_working_directory, dataset_collection_element, **kwargs)
            else:
                element_wrapper = self._dataset_wrapper(element_object, identifier=element_identifier, **kwargs)

            element_instances[element_identifier] = element_wrapper
            element_instance_list.append(element_wrapper)

        self.__element_instances = element_instances
        self.__element_instance_list = element_instance_list

    def get_datasets_for_group(self, group):
        group = str(group).lower()
        if not self._dataset_elements_cache.get(group):
            wrappers = []
            for element in self.collection.dataset_elements:
                if any([t for t in element.dataset_instance.tags if t.user_tname.lower() == 'group' and t.value.lower() == group]):
                    wrappers.append(self._dataset_wrapper(element.element_object, identifier=element.element_identifier, **self.kwargs))
            self._dataset_elements_cache[group] = wrappers
        return self._dataset_elements_cache[group]

    def keys(self):
        if not self.__input_supplied:
            return []
        return self.__element_instances.keys()

    @property
    def is_collection(self):
        return True

    @property
    def element_identifier(self):
        return self.name

    @property
    def all_paths(self):
        return [path for _, _, path, _ in self.element_identifiers_extensions_paths_and_metadata_files]

    @property
    def all_metadata_files(self):
        return [metadata_files for _, _, _, metadata_files in self.element_identifiers_extensions_paths_and_metadata_files]

    @property
    def element_identifiers_extensions_paths_and_metadata_files(self):
        if self._element_identifiers_extensions_paths_and_metadata_files is None:
            if self.collection:
                self._element_identifiers_extensions_paths_and_metadata_files = self.collection.element_identifiers_extensions_paths_and_metadata_files
            else:
                return []
        return self._element_identifiers_extensions_paths_and_metadata_files

    def get_all_staging_paths(self, invalid_chars=('/',), include_collection_name=False):
        safe_element_identifiers = []
        for element_identifiers, extension, *_ in self.element_identifiers_extensions_paths_and_metadata_files:
            datatype = self.datatypes_registry.get_datatype_by_extension(extension)
            if datatype:
                extension = getattr(datatype, 'file_ext_export_alias', extension)
            current_element_identifiers = []
            for element_identifier in element_identifiers:
                max_len = 254 - len(extension)
                if include_collection_name:
                    max_len = max_len - (len(self.name) + 1)
                    assert max_len >= 1, 'Could not stage element, element identifier is too long'
                current_element_identifier = filesystem_safe_string(element_identifier, max_len=max_len, invalid_chars=invalid_chars)
                if include_collection_name and self.name:
                    current_element_identifier = f"{filesystem_safe_string(self.name, invalid_chars=invalid_chars)}{os.path.sep}{current_element_identifier}"
                current_element_identifiers.append(current_element_identifier)

            safe_element_identifiers.append(f'{os.path.sep.join(current_element_identifiers)}.{extension}')
        return safe_element_identifiers

    def serialize(self, invalid_chars=('/',), include_collection_name=False):
        return data_collection_input_to_staging_path_and_source_path(self, invalid_chars=invalid_chars, include_collection_name=include_collection_name)

    @property
    def is_input_supplied(self):
        return self.__input_supplied

    def __getitem__(self, key):
        if not self.__input_supplied:
            return None
        if isinstance(key, int):
            return self.__element_instance_list[key]
        else:
            return self.__element_instances[key]

    def __getattr__(self, key):
        if not self.__input_supplied:
            return None
        try:
            return self.__element_instances[key]
        except KeyError:
            raise AttributeError()

    def __iter__(self):
        if not self.__input_supplied:
            return [].__iter__()
        return self.__element_instance_list.__iter__()

    def __bool__(self):
        # Fail `#if $param` checks in cheetah is optional input
        # not specified or if resulting collection is empty.
        return self.__input_supplied and bool(self.__element_instance_list)
    __nonzero__ = __bool__


class ElementIdentifierMapper:
    """Track mapping of dataset collection elements datasets to element identifiers."""

    def __init__(self, input_datasets=None):
        if input_datasets is not None:
            self.identifier_key_dict = {v: f"{k}|__identifier__" for k, v in input_datasets.items()}
        else:
            self.identifier_key_dict = {}

    def identifier(self, dataset_value, input_values):
        identifier_key = self.identifier_key_dict.get(dataset_value, None)
        element_identifier = None
        if identifier_key:
            element_identifier = input_values.get(identifier_key, None)

        return element_identifier
