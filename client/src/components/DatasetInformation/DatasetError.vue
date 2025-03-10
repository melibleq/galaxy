<template>
    <div>
        <b-alert v-if="errorMessage" variant="danger" show>
            <h4 class="alert-heading">Failed to access Dataset details.</h4>
            {{ errorMessage }}
        </b-alert>
        <DatasetProvider :id="datasetId" v-slot="{ item: dataset, loading: datasetLoading }">
            <JobDetailsProvider
                v-if="!datasetLoading"
                :jobid="dataset.creating_job"
                v-slot="{ result: jobDetails, loading }"
                @error="onError"
            >
                <div v-if="!loading">
                    <div class="page-container edit-attr">
                        <div class="response-message"></div>
                    </div>
                    <h2>Dataset Error Report</h2>
                    <p>
                        An error occurred while running the tool
                        <b id="dataset-error-tool-id" class="text-break">{{ jobDetails.tool_id }}</b
                        >.
                    </p>
                    <div v-if="jobDetails.tool_stderr || jobDetails.job_stderr || jobDetails.job_messages">
                        <h3>Details</h3>
                    </div>
                    <div v-if="jobDetails.job_messages" id="dataset-error-job-messages">
                        <p>Execution resulted in the following messages:</p>
                        <div v-for="(job_message, index) in jobDetails.job_messages" :key="index">
                            <pre class="rounded code">{{ job_message["desc"] }}</pre>
                        </div>
                    </div>
                    <div v-if="jobDetails.tool_stderr">
                        <p>Tool generated the following standard error:</p>
                        <pre id="dataset-error-tool-stderr" class="rounded code">{{ jobDetails.tool_stderr }}</pre>
                    </div>
                    <div v-if="jobDetails.job_stderr">
                        <p>Galaxy job runner generated the following standard error:</p>
                        <pre id="dataset-error-job-stderr" class="rounded code">{{ jobDetails.job_stderr }}</pre>
                    </div>
                    <JobProblemProvider :jobid="dataset.creating_job" v-slot="{ result: jobProblems }" @error="onError">
                        <div v-if="jobProblems && (jobProblems.has_duplicate_inputs || jobProblems.has_empty_inputs)">
                            <h3 class="common_problems mt-3">Detected Common Potential Problems</h3>
                            <p v-if="jobProblems.has_empty_inputs" id="dataset-error-has-empty-inputs">
                                The tool was executed with one or more empty input datasets. This frequently results in
                                tool errors due to problematic input choices.
                            </p>
                            <p v-if="jobProblems.has_duplicate_inputs" id="dataset-error-has-duplicate-inputs">
                                The tool was executed with one or more duplicate input datasets. This frequently results
                                in tool errors due to problematic input choices.
                            </p>
                        </div>
                    </JobProblemProvider>
                    <h3 class="mt-3">Troubleshooting</h3>
                    <p>
                        There are a number of helpful resources to self diagnose and correct problems.
                        <br />
                        Start here:
                        <b>
                            <a href="https://galaxyproject.org/support/tool-error/" target="_blank">
                                My job ended with an error. What can I do?
                            </a>
                        </b>
                    </p>
                    <h3 class="mb-3">Issue Report</h3>
                    <b-alert
                        v-for="(resultMessage, index) in resultMessages"
                        :key="index"
                        :variant="resultMessage[1]"
                        show
                        >{{ resultMessage[0] }}</b-alert
                    >
                    <FormElement
                        v-if="!jobDetails.user_email"
                        id="dataset-error-email"
                        v-model="email"
                        title="Please provide your email:"
                    />
                    <FormElement
                        id="dataset-error-message"
                        v-model="message"
                        :area="true"
                        title="Please provide detailed information on the activities leading to this issue:"
                    />
                    <b-button
                        id="dataset-error-submit"
                        variant="primary"
                        @click="submit(dataset, jobDetails.user_email)"
                        class="mt-3"
                    >
                        <font-awesome-icon icon="bug" class="mr-1" />Report
                    </b-button>
                </div>
            </JobDetailsProvider>
        </DatasetProvider>
    </div>
</template>

<script>
import FormElement from "components/Form/FormElement";
import { DatasetProvider } from "components/WorkflowInvocationState/providers";
import { JobDetailsProvider, JobProblemProvider } from "components/providers/JobProvider";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { library } from "@fortawesome/fontawesome-svg-core";
import { faBug } from "@fortawesome/free-solid-svg-icons";
import { sendErrorReport } from "./services";

library.add(faBug);

export default {
    components: {
        DatasetProvider,
        FontAwesomeIcon,
        FormElement,
        JobDetailsProvider,
        JobProblemProvider,
    },
    props: {
        datasetId: {
            type: String,
            required: true,
        },
    },
    data() {
        return {
            message: null,
            email: null,
            errorMessage: null,
            resultMessages: [],
        };
    },
    methods: {
        onError(err) {
            this.errorMessage = err;
        },
        submit(dataset, userEmail) {
            const email = this.email || userEmail;
            const message = this.message;
            sendErrorReport(dataset, message, email).then(
                (resultMessages) => {
                    this.resultMessages = resultMessages;
                },
                (errorMessage) => {
                    this.errorMessage = errorMessage;
                }
            );
        },
    },
};
</script>
