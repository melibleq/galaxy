import logging
import os
from enum import Enum
from typing import Dict, List, Optional, Tuple

from galaxy.tool_util.edam_util import (
    load_edam_tree,
    ROOT_OPERATION,
    ROOT_TOPIC,
)
from galaxy.util import (
    etree,
    ExecutionTimer,
)
from .interface import (
    ToolBoxRegistry,
    ToolPanelView,
    ToolPanelViewModel,
    ToolPanelViewModelType,
    walk_loaded_tools,
)
from ..panel import (
    ToolPanelElements,
    ToolSectionLabel,
)

log = logging.getLogger(__name__)


class EdamPanelMode(str, Enum):
    merged = "merged"
    topics = "topics"
    operations = "operations"


class EdamToolPanelView(ToolPanelView):

    def __init__(self, edam_ontology_path: Optional[str], mode: EdamPanelMode = EdamPanelMode.merged):
        edam = load_edam_tree(None if not edam_ontology_path or not os.path.exists(edam_ontology_path) else edam_ontology_path)
        self.edam = edam
        self.mode = mode
        self.include_topics = mode in [EdamPanelMode.merged, EdamPanelMode.topics]
        self.include_operations = mode in [EdamPanelMode.merged, EdamPanelMode.operations]

    def apply_view(self, base_tool_panel: ToolPanelElements, toolbox_registry: ToolBoxRegistry) -> ToolPanelElements:
        execution_timer = ExecutionTimer()

        # Find the children of the top level topics
        operations_list = [ROOT_OPERATION] + list(self._edam_children_of(ROOT_OPERATION))
        topics_list = [ROOT_TOPIC] + list(self._edam_children_of(ROOT_TOPIC))

        # Sort them (by english label)
        # operations = sorted(operations, key=lambda x: self.edam[x]['label'])
        # topics = sorted(topics, key=lambda x: self.edam[x]['label'])

        # Convert these to list of dicts, wherein we'll add our tools/etc.
        operations: Dict[str, Dict] = {
            x: {}
            for x in operations_list
        }
        topics: Dict[str, Dict] = {
            x: {}
            for x in topics_list
        }
        uncategorized: List[Tuple] = []

        for tool_id, key, val, val_name in walk_loaded_tools(base_tool_panel, toolbox_registry):
            for term in self._get_edam_sec(val):
                if term == 'uncategorized':
                    uncategorized.append((tool_id, key, val, val_name))
                else:
                    for path in self.edam[term]['path']:
                        if len(path) == 1:
                            t = term
                        else:
                            t = path[0]

                        if path[0].startswith('operation_'):
                            operations[t][tool_id] = (term, tool_id, key, val, val_name)
                        elif path[0].startswith('topic_'):
                            topics[t][tool_id] = (term, tool_id, key, val, val_name)

        new_panel = ToolPanelElements()
        for term in sorted(operations.keys(), key=lambda x: self._sort_edam_key(x)):
            if len(operations[term].keys()) == 0:
                continue

            elem = etree.Element('label')
            elem.attrib['text'] = self.edam[term]['label']
            elem.attrib['id'] = term
            new_panel[f"label_{term}"] = ToolSectionLabel(elem)

            for (term, tool_id, key, val, val_name) in operations[term].values():
                section = new_panel.get_or_create_section(term, self.edam[term]['label'])

                toolbox_registry.add_tool_to_tool_panel_view(val, section)
                new_panel.record_section_for_tool_id(tool_id, key, val_name)

        for term in sorted(topics.keys(), key=lambda x: self._sort_edam_key(x)):
            if len(topics[term].keys()) == 0:
                continue

            elem = etree.Element('label')
            elem.attrib['text'] = self.edam[term]['label']
            elem.attrib['id'] = term
            new_panel[f"label_{term}"] = ToolSectionLabel(elem)

            for (term, tool_id, key, val, val_name) in topics[term].values():
                section = new_panel.get_or_create_section(term, self.edam[term]['label'])
                toolbox_registry.add_tool_to_tool_panel_view(val, section)
                new_panel.record_section_for_tool_id(tool_id, key, val_name)

        section = new_panel.get_or_create_section('uncategorized', 'Uncategorized')
        for (tool_id, key, val, val_name) in uncategorized:
            toolbox_registry.add_tool_to_tool_panel_view(val, section)
            new_panel.record_section_for_tool_id(tool_id, key, val_name)
        log.debug("Loading EDAM tool panel finished %s", execution_timer)
        return new_panel

    def _get_edam_sec(self, tool):
        edam = []
        if self.include_operations:
            edam.extend(tool.edam_operations)
        if self.include_topics:
            edam.extend(tool.edam_topics)
        if len(edam) > 0:
            for term in edam:
                yield term
        else:
            yield 'uncategorized'

    def _sort_edam_key(self, x):
        if x in (ROOT_OPERATION, ROOT_TOPIC):
            return f"!{x}"
        else:
            return self.edam[x]['label']

    def _edam_children_of(self, parentTerm):
        for term in self.edam.keys():
            if parentTerm in self.edam[term]['parents']:
                yield term

    def to_model(self) -> ToolPanelViewModel:
        mode = self.mode
        if mode == EdamPanelMode.merged:
            model_id = "ontology:edam_merged"
            name = "EDAM Operations and Topics"
            description = "Tools are grouped using both annotated operation and topic information (if availabled)."
        elif mode == EdamPanelMode.operations:
            model_id = "ontology:edam_operations"
            name = "EDAM Operations"
            description = "Tools are grouped using annotated EDAM operation information (if availabled)."
        elif mode == EdamPanelMode.topics:
            model_id = "ontology:edam_topics"
            name = "EDAM Topics"
            description = "Tools are grouped using annotated EDAM topic information (if availabled)."
        else:
            raise AssertionError(f"Invalid EDAM mode encountered {mode}")
        model_class = self.__class__.__name__
        view_type = ToolPanelViewModelType.ontology
        return ToolPanelViewModel(
            id=model_id,
            name=name,
            description=description,
            model_class=model_class,
            view_type=view_type,
            searchable=True,
        )
