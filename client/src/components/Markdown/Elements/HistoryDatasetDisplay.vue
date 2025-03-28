<template>
    <b-card body-class="p-0">
        <b-card-header v-if="!embedded">
            <span class="float-right">
                <b-button
                    :href="downloadUrl"
                    variant="link"
                    size="sm"
                    role="button"
                    title="Download Dataset"
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
                    title="Import Dataset"
                    type="button"
                    class="py-0 px-1"
                    v-b-tooltip.hover
                >
                    <span class="fa fa-file-import" />
                </b-button>
            </span>
            <span>
                <span>Dataset:</span>
                <span class="font-weight-light">{{ datasetName }}</span>
            </span>
        </b-card-header>
        <b-card-body>
            <UrlDataProvider :url="itemUrl" v-slot="{ result: itemContent, loading, error }">
                <LoadingSpan v-if="loading" message="Loading Dataset" />
                <div v-else-if="error">{{ error }}</div>
                <div v-else class="embedded-dataset content-height">
                    <UrlDataProvider :url="datatypesUrl" v-slot="{ result: datatypesModel, loading: datatypesLoading }">
                        <LoadingSpan v-if="datatypesLoading" message="Loading Datatypes" />
                        <div v-else>
                            <b-embed
                                v-if="isSubTypeOfAny(datasetType, ['pdf', 'html'], datatypesModel)"
                                type="iframe"
                                aspect="16by9"
                                :src="displayUrl"
                            />
                            <div v-else-if="itemContent.item_data">
                                <div v-if="isSubTypeOfAny(datasetType, ['tabular'], datatypesModel)">
                                    <UrlDataProvider
                                        :url="metaUrl"
                                        v-slot="{ result: metaData, loading: metaLoading, error: metaError }"
                                    >
                                        <LoadingSpan v-if="metaLoading" message="Loading Metadata" />
                                        <div v-else-if="metaError">{{ metaError }}</div>
                                        <b-table
                                            v-else
                                            striped
                                            hover
                                            :fields="getFields(metaData)"
                                            :items="getItems(itemContent.item_data, metaData)"
                                        />
                                    </UrlDataProvider>
                                </div>
                                <pre v-else>
                                    <code class="text-normalwrap">{{ itemContent.item_data }}</code>
                                </pre>
                            </div>
                            <div v-else>No content found.</div>
                            <b-link v-if="itemContent.truncated" :href="itemContent.item_url"> Show More... </b-link>
                        </div>
                    </UrlDataProvider>
                </div>
            </UrlDataProvider>
        </b-card-body>
    </b-card>
</template>

<script>
import { getAppRoot } from "onload/loadConfig";
import LoadingSpan from "components/LoadingSpan";
import { UrlDataProvider } from "components/providers/UrlDataProvider";
import { DatatypesMapperModel } from "components/Datatypes/model";

export default {
    components: {
        LoadingSpan,
        UrlDataProvider,
    },
    props: {
        args: {
            type: Object,
            required: true,
        },
        datasets: {
            type: Object,
            required: true,
        },
        embedded: {
            type: Boolean,
            default: false,
        },
    },
    computed: {
        datasetType() {
            const dataset = this.datasets[this.args.history_dataset_id];
            return dataset.ext;
        },
        datasetName() {
            const dataset = this.datasets[this.args.history_dataset_id];
            return dataset && dataset.name;
        },
        datatypesUrl() {
            return "api/datatypes/types_and_mapping";
        },
        downloadUrl() {
            return `${getAppRoot()}dataset/display?dataset_id=${this.args.history_dataset_id}`;
        },
        displayUrl() {
            return `${getAppRoot()}datasets/${this.args.history_dataset_id}/display`;
        },
        importUrl() {
            return `${getAppRoot()}dataset/imp?dataset_id=${this.args.history_dataset_id}`;
        },
        itemUrl() {
            return `api/datasets/${this.args.history_dataset_id}/get_content_as_text`;
        },
        metaUrl() {
            return `api/datasets/${this.args.history_dataset_id}`;
        },
    },
    methods: {
        isSubTypeOfAny(child, parents, datatypesModel) {
            const datatypesMapper = new DatatypesMapperModel(datatypesModel);
            return datatypesMapper.isSubTypeOfAny(child, parents);
        },
        getFields(metaData) {
            const fields = [];
            const columnNames = metaData.metadata_column_names || [];
            const columnCount = metaData.metadata_columns;
            for (let i = 0; i < columnCount; i++) {
                fields.push({
                    key: `${i}`,
                    label: columnNames[i] || i,
                    sortable: true,
                });
            }
            return fields;
        },
        getItems(textData, metaData) {
            const tableData = [];
            const delimiter = metaData.metadata_delimiter || "\t";
            const comments = metaData.metadata_comment_lines || 0;
            const lines = textData.split("\n");
            lines.forEach((line, i) => {
                if (i >= comments) {
                    const tabs = line.split(delimiter);
                    const rowData = {};
                    let hasData = false;
                    tabs.forEach((cellData, j) => {
                        const cellDataTrimmed = cellData.trim();
                        if (cellDataTrimmed) {
                            hasData = true;
                        }
                        rowData[j] = cellDataTrimmed;
                    });
                    if (hasData) {
                        tableData.push(rowData);
                    }
                }
            });
            return tableData;
        },
    },
};
</script>
<style scoped>
.content-height {
    max-height: 20rem;
}
</style>
