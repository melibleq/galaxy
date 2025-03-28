<template>
    <div>
        <ServerSelection
            :toolshed-url="toolshedUrl"
            :toolshed-urls="toolshedUrls"
            :total="total"
            :loading="loading"
            @onToolshed="setToolshed"
        />
        <div v-if="error" class="alert alert-danger">{{ error }}</div>
        <div v-else>
            <Repositories
                :query="query"
                :scrolled="scrolled"
                :toolshed-url="toolshedUrl"
                @onError="setError"
                v-if="!queryEmpty"
            />
            <Categories
                :toolshed-url="toolshedUrl"
                :loading="loading"
                @onCategory="setQuery"
                @onTotal="setTotal"
                @onError="setError"
                @onLoading="setLoading"
                v-show="queryEmpty"
            />
        </div>
    </div>
</template>
<script>
import { getGalaxyInstance } from "app";
import Categories from "./Categories.vue";
import Repositories from "./Repositories.vue";
import ServerSelection from "./ServerSelection.vue";
export default {
    props: {
        query: {
            type: String,
            required: true,
        },
        scrolled: {
            type: Boolean,
            required: true,
        },
    },
    components: {
        Categories,
        Repositories,
        ServerSelection,
    },
    data() {
        return {
            toolshedUrl: null,
            toolshedUrls: [],
            queryLength: 3,
            loading: false,
            total: 0,
            error: null,
            tabCurrent: "true",
            tabOptions: [
                { text: "Search All", value: true },
                { text: "Installed Only", value: false },
            ],
        };
    },
    created() {
        this.configureToolsheds();
    },
    computed: {
        queryEmpty() {
            return !this.query || this.query.length < this.queryLength;
        },
    },
    methods: {
        configureToolsheds() {
            const galaxy = getGalaxyInstance();
            this.toolshedUrls = galaxy.config.tool_shed_urls;
            if (!this.toolshedUrls || this.toolshedUrls.length == 0) {
                this.setError("Toolshed registry is empty, no servers found.");
            } else {
                this.toolshedUrl = this.toolshedUrls[0];
            }
        },
        setError(error) {
            this.error = error;
        },
        setQuery(query) {
            this.$emit("onQuery", query);
        },
        setToolshed(url) {
            this.error = null;
            this.toolshedUrl = url;
        },
        setTotal(total) {
            this.total = total;
        },
        setLoading(loading) {
            this.loading = loading;
        },
    },
};
</script>
