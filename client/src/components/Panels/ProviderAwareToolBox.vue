<template>
    <ConfigProvider class="d-flex flex-column" v-slot="{ config }">
        <ToolPanelViewProvider
            v-slot="{ currentPanel, currentPanelView }"
            :site-default-panel-view="config.default_panel_view"
            v-if="config.default_panel_view"
        >
            <ToolBox
                :toolbox="currentPanel"
                :panel-views="config.panel_views"
                :current-panel-view="currentPanelView"
                :stored-workflow-menu-entries="storedWorkflowMenuEntries"
                :workflow-title="workflowTitle"
                @updatePanelView="updatePanelView"
                v-if="currentPanelView"
            >
            </ToolBox>
        </ToolPanelViewProvider>
    </ConfigProvider>
</template>

<script>
import ToolBox from "./ToolBox";
import ConfigProvider from "components/providers/ConfigProvider";
import ToolPanelViewProvider from "components/providers/ToolPanelViewProvider";
import _l from "utils/localization";
import { mapActions } from "vuex";

export default {
    components: {
        ConfigProvider,
        ToolBox,
        ToolPanelViewProvider,
    },
    props: {
        storedWorkflowMenuEntries: {
            type: Array,
            required: true,
        },
        workflowTitle: {
            type: String,
            default: _l("Workflows"),
        },
    },
    methods: {
        updatePanelView(panelView) {
            this.setCurrentPanelView(panelView);
        },
        ...mapActions("panels", ["setCurrentPanelView"]),
    },
};
</script>
