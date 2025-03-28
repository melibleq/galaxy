<template>
    <b-card body-class="p-0">
        <b-card-header v-if="!embedded">
            <span class="float-right">
                <b-button
                    :href="downloadUrl"
                    variant="link"
                    size="sm"
                    role="button"
                    title="Download Workflow"
                    type="button"
                    class="py-0 px-1"
                    v-b-tooltip.hover
                >
                    <span class="fa fa-download" />
                </b-button>
                <b-button
                    :href="importUrl"
                    role="button"
                    variant="link"
                    title="Import Workflow"
                    type="button"
                    class="py-0 px-1"
                    v-b-tooltip.hover
                >
                    <span class="fa fa-file-import" />
                </b-button>
            </span>
            <span>
                <span>Workflow:</span>
                <span class="font-weight-light">{{ workflowName }}</span>
            </span>
        </b-card-header>
        <b-card-body>
            <LoadingSpan v-if="loading" message="Loading Workflow" />
            <div v-else class="content-height">
                <div v-for="step in itemContent.steps" :key="step.order_index" class="mb-2">
                    <div>Step {{ step.order_index + 1 }}: {{ step.label }}</div>
                    <WorkflowTree :input="step" :skip-head="true" />
                </div>
            </div>
        </b-card-body>
    </b-card>
</template>

<script>
import { getAppRoot } from "onload/loadConfig";
import LoadingSpan from "components/LoadingSpan";
import WorkflowTree from "./WorkflowTree";
import axios from "axios";
export default {
    components: {
        LoadingSpan,
        WorkflowTree,
    },
    props: {
        args: {
            type: Object,
            required: true,
        },
        workflows: {
            type: Object,
            required: true,
        },
        embedded: {
            type: Boolean,
            default: false,
        },
    },
    data() {
        return {
            itemContent: null,
            loading: true,
        };
    },
    created() {
        this.getContent().then((data) => {
            this.itemContent = data;
            this.loading = false;
        });
    },
    computed: {
        workflowName() {
            const workflow = this.workflows[this.args.workflow_id];
            return workflow && workflow.name;
        },
        downloadUrl() {
            return `${getAppRoot()}api/workflows/${this.args.workflow_id}/download?format=json-download`;
        },
        importUrl() {
            return `${getAppRoot()}workflow/imp?id=${this.args.workflow_id}`;
        },
        itemUrl() {
            return `${getAppRoot()}api/workflows/${this.args.workflow_id}/download?style=preview`;
        },
    },
    methods: {
        async getContent() {
            try {
                const response = await axios.get(this.itemUrl);
                return response.data;
            } catch (e) {
                return `Failed to retrieve content. ${e}`;
            }
        },
    },
};
</script>
<style scoped>
.content-height {
    max-height: 15rem;
}
</style>
