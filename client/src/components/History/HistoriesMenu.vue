<!-- menu allowing user to change the current history, make a new one, basically anything that's
"above" editing the current history -->

<template>
    <div>
        <b-dropdown
            size="sm"
            variant="link"
            :text="title | l"
            toggle-class="text-decoration-none"
            no-caret
            class="histories-operation-menu"
            data-description="histories operation menu"
        >
            <template v-slot:button-content>
                <Icon class="mr-1" icon="folder" />
                <span class="text-nowrap">{{ title | l }}</span>
            </template>

            <b-dropdown-text>
                <span>You have {{ histories.length }} histories.</span>
            </b-dropdown-text>

            <b-dropdown-divider></b-dropdown-divider>

            <b-dropdown-item v-b-modal.history-selector-modal>
                <Icon class="mr-1" icon="exchange-alt" />
                <span v-localize>Change the Current History</span>
            </b-dropdown-item>

            <b-dropdown-item data-description="create new history" @click="$emit('createNewHistory')">
                <Icon class="mr-1" icon="plus" />
                <span v-localize>Create a New History</span>
            </b-dropdown-item>

            <b-dropdown-item @click="backboneRoute('/histories/list')">
                <Icon class="mr-1" icon="list" />
                <span v-localize>View Saved Histories</span>
            </b-dropdown-item>

            <b-dropdown-item @click="redirect('/history/view_multiple')">
                <Icon class="mr-1" icon="columns" />
                <span v-localize>Show Histories Side-by-Side</span>
            </b-dropdown-item>

            <b-dropdown-divider></b-dropdown-divider>

            <b-dropdown-item @click="switchToLegacyHistoryPanel">
                <Icon class="mr-1" icon="arrow-up" />
                <span v-localize>Return to legacy panel</span>
            </b-dropdown-item>
        </b-dropdown>

        <!-- modals -->
        <HistorySelectorModal
            id="history-selector-modal"
            :histories="histories"
            :current-history="currentHistory"
            @selectHistory="$emit('setCurrentHistory', $event)"
        />
    </div>
</template>

<script>
import { History } from "./model";
import { legacyNavigationMixin } from "components/plugins/legacyNavigation";
import { switchToLegacyHistoryPanel } from "./adapters/betaToggle";
import HistorySelectorModal from "./HistorySelectorModal";

export default {
    mixins: [legacyNavigationMixin],
    components: {
        HistorySelectorModal,
    },
    props: {
        histories: { type: Array, required: true },
        currentHistory: { type: History, required: true },
        title: { type: String, required: false, default: "Histories" },
    },
    methods: {
        switchToLegacyHistoryPanel,
    },
};
</script>
