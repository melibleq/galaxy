/** define the 'Analyze Data'/analysis/main/home page for Galaxy
 *  * has a masthead
 *  * a left tool menu to allow the user to load tools in the center panel
 *  * a right history menu that shows the user's current data
 *  * a center panel
 *  Both panels (generally) persist while the center panel shows any
 *  UI needed for the current step of an analysis, like:
 *      * tool forms to set tool parameters,
 *      * tables showing the contents of datasets
 *      * etc.
 */

import _ from "underscore";
import { getGalaxyInstance } from "app";
import { getAppRoot } from "onload";
import decodeUriComponent from "decode-uri-component";
import Router from "layout/router";
import ToolForm from "components/Tool/ToolForm";
import FormGeneric from "components/Form/FormGeneric";
import Sharing from "components/Sharing.vue";
import UserPreferences from "components/User/UserPreferences.vue";
import DatasetList from "components/Dataset/DatasetList.vue";
import { getUserPreferencesModel } from "components/User/UserPreferencesModel";
import CustomBuilds from "components/User/CustomBuilds.vue";
import Tours from "mvc/tours";
import GridView from "mvc/grid/grid-view";
import GridShared from "mvc/grid/grid-shared";
import WorkflowImport from "components/Workflow/WorkflowImport.vue";
import TrsImport from "components/Workflow/TrsImport.vue";
import TrsSearch from "components/Workflow/TrsSearch.vue";
import InteractiveTools from "components/InteractiveTools/InteractiveTools.vue";
import WorkflowList from "components/Workflow/WorkflowList.vue";
import HistoryImport from "components/HistoryImport.vue";
import { HistoryExport } from "components/HistoryExport/index";
import HistoryView from "components/HistoryView.vue";
import WorkflowInvocationReport from "components/Workflow/InvocationReport.vue";
import WorkflowRun from "components/Workflow/Run/WorkflowRun.vue";
import RecentInvocations from "components/User/RecentInvocations.vue";
import ToolsView from "components/ToolsView/ToolsView.vue";
import ToolsJson from "components/ToolsView/ToolsSchemaJson/ToolsJson.vue";
import HistoryList from "mvc/history/history-list";
import PluginList from "components/PluginList.vue";
import QueryStringParsing from "utils/query-string-parsing";
import DatasetError from "components/DatasetInformation/DatasetError";
import DatasetAttributes from "components/DatasetInformation/DatasetAttributes";
import Citations from "components/Citation/Citations.vue";
import DisplayStructure from "components/DisplayStructured.vue";
import { CloudAuth } from "components/User/CloudAuth";
import { ExternalIdentities } from "components/User/ExternalIdentities";
import Confirmation from "components/login/Confirmation.vue";
import Libraries from "components/Libraries";
import { mountVueComponent } from "utils/mountVueComponent";

/** Routes */
export const getAnalysisRouter = (Galaxy) => {
    return Router.extend({
        routes: {
            "(/)(#)(_=_)": "home",
            "(/)root*": "home",
            "(/)login/confirm": "show_new_user_confirmation",
            "(/)tools/view": "show_tools_view",
            "(/)tools/json": "show_tools_json",
            "(/)tours(/)(:tour_id)": "show_tours",
            "(/)user(/)": "show_user",
            "(/)user(/)cloud_auth": "show_cloud_auth",
            "(/)user(/)external_ids": "show_external_ids",
            "(/)user(/)(:form_id)": "show_user_form",
            "(/)pages(/)create(/)": "show_pages_create",
            "(/)pages(/)edit(/)": "show_pages_edit",
            "(/)pages(/)sharing(/)": "show_pages_sharing",
            "(/)pages(/)(:action_id)": "show_pages",
            "(/)visualizations(/)": "show_plugins",
            "(/)visualizations(/)edit(/)": "show_visualizations_edit",
            "(/)visualizations(/)sharing(/)": "show_visualizations_sharing",
            "(/)visualizations/(:action_id)": "show_visualizations",
            "(/)workflows/import": "show_workflows_import",
            "(/)workflows/trs_import": "show_workflows_trs_import",
            "(/)workflows/trs_search": "show_workflows_trs_search",
            "(/)workflows/run(/)": "show_workflows_run",
            "(/)workflows(/)list": "show_workflows",
            "(/)workflows/invocations": "show_workflow_invocations",
            "(/)workflows/invocations/report": "show_workflow_invocation_report",
            // "(/)workflows/invocations/view_bco": "show_invocation_bco",
            "(/)workflows/list_published(/)": "show_workflows_published",
            "(/)workflows/create(/)": "show_workflows_create",
            "(/)histories(/)citations(/)": "show_history_citations",
            "(/)histories(/)rename(/)": "show_histories_rename",
            "(/)histories(/)sharing(/)": "show_histories_sharing",
            "(/)histories(/)import(/)": "show_histories_import",
            "(/)histories(/)(:history_id)(/)export(/)": "show_history_export",
            "(/)histories(/)permissions(/)": "show_histories_permissions",
            "(/)histories/view": "show_history_view",
            "(/)histories/show_structure": "show_history_structure",
            "(/)histories(/)(:action_id)": "show_histories",
            "(/)datasets(/)list(/)": "show_datasets",
            "(/)custom_builds": "show_custom_builds",
            "(/)datasets/edit": "show_dataset_edit_attributes",
            "(/)datasets/error": "show_dataset_error",
            "(/)interactivetool_entry_points(/)list": "show_interactivetool_list",
            "(/)libraries*path": "show_library_folder",
        },

        require_login: ["show_user", "show_user_form", "show_workflows", "show_cloud_auth", "show_external_ids"],

        authenticate: function (args, name) {
            const Galaxy = getGalaxyInstance();
            return (Galaxy.user && Galaxy.user.id) || this.require_login.indexOf(name) == -1;
        },

        _display_vue_helper: function (component, propsData = {}, active_tab = null, noPadding = false) {
            const container = document.createElement("div");
            if (active_tab) {
                container.active_tab = active_tab;
            }
            this.page.display(container, noPadding);
            const mountFn = mountVueComponent(component);
            return mountFn(propsData, container);
        },

        show_tours: function (tour_id) {
            if (tour_id) {
                Tours.giveTourById(tour_id);
            } else {
                this.page.display(new Tours.ToursView());
            }
        },

        show_new_user_confirmation: function () {
            this._display_vue_helper(Confirmation);
        },

        show_user: function () {
            const Galaxy = getGalaxyInstance();
            this._display_vue_helper(UserPreferences, {
                enableQuotas: Galaxy.config.enable_quotas,
                userId: Galaxy.user.id,
            });
        },

        show_user_form: function (form_id, params) {
            const model = getUserPreferencesModel(params.id);
            this._display_vue_helper(FormGeneric, _.extend(model[form_id], { active_tab: "user" }));
        },

        show_interactivetool_list: function () {
            this._display_vue_helper(InteractiveTools);
        },

        show_library_folder: function () {
            this.page.toolPanel?.component.hide(0);
            this.page.panels.right.hide();
            this._display_vue_helper(Libraries);
        },

        show_cloud_auth: function () {
            this._display_vue_helper(CloudAuth);
        },

        show_external_ids: function () {
            this._display_vue_helper(ExternalIdentities);
        },

        show_visualizations: function (action_id) {
            const activeTab = action_id == "list_published" ? "shared" : "user";
            this.page.display(
                new GridShared.View({
                    action_id: action_id,
                    plural: "Visualizations",
                    item: "visualization",
                    active_tab: activeTab,
                })
            );
        },

        show_visualizations_edit: function () {
            this._display_vue_helper(FormGeneric, {
                url: `visualization/edit?id=${QueryStringParsing.get("id")}`,
                redirect: "visualizations/list",
                active_tab: "visualization",
            });
        },

        show_visualizations_sharing: function () {
            this._display_vue_helper(Sharing, {
                id: QueryStringParsing.get("id"),
                plural_name: "Visualizations",
                model_class: "Visualization",
            });
        },

        show_workflows_published: function () {
            const userFilter = QueryStringParsing.get("f-username");
            this.page.display(
                new GridView({
                    url_base: `${getAppRoot()}workflow/list_published`,
                    active_tab: "shared",
                    url_data: {
                        "f-username": userFilter == null ? "" : userFilter,
                    },
                })
            );
        },

        show_history_view: function () {
            this._display_vue_helper(HistoryView, { id: QueryStringParsing.get("id") });
        },

        show_workflow_invocation_report: function () {
            const invocationId = QueryStringParsing.get("id");
            this._display_vue_helper(WorkflowInvocationReport, { invocationId: invocationId }, null, true);
        },

        show_workflow_invocations: function () {
            this._display_vue_helper(RecentInvocations, {});
        },

        show_history_structure: function () {
            this._display_vue_helper(DisplayStructure, { id: QueryStringParsing.get(" id: ") });
        },

        show_histories: function (action_id) {
            const view = new HistoryList.View({ action_id: action_id });
            this.page.display(view);
        },

        show_history_citations: function () {
            this._display_vue_helper(Citations, { id: QueryStringParsing.get("id"), source: "histories" });
        },

        show_histories_rename: function () {
            this._display_vue_helper(FormGeneric, {
                url: `history/rename?id=${QueryStringParsing.get("id")}`,
                redirect: "histories/list",
            });
        },

        show_histories_sharing: function () {
            this._display_vue_helper(Sharing, {
                id: QueryStringParsing.get("id"),
                plural_name: "Histories",
                model_class: "History",
            });
        },

        show_histories_import: function () {
            this._display_vue_helper(HistoryImport);
        },

        show_history_export: function (history_id) {
            this._display_vue_helper(HistoryExport, {
                historyId: history_id,
            });
        },

        show_tools_view: function () {
            this.page.toolPanel?.component.hide();
            this.page.panels.right.hide();
            this._display_vue_helper(ToolsView);
        },

        show_tools_json: function () {
            this._display_vue_helper(ToolsJson);
        },

        show_histories_permissions: function () {
            this._display_vue_helper(FormGeneric, {
                url: `history/permissions?id=${QueryStringParsing.get("id")}`,
                redirect: "histories/list",
            });
        },

        show_datasets: function () {
            this._display_vue_helper(DatasetList);
        },

        show_pages: function (action_id) {
            const activeTab = action_id == "list_published" ? "shared" : "user";
            this.page.display(
                new GridShared.View({
                    action_id: action_id,
                    plural: "Pages",
                    item: "page",
                    active_tab: activeTab,
                })
            );
        },

        show_pages_create: function () {
            let url = "page/create";
            const invocation_id = QueryStringParsing.get("invocation_id");
            if (invocation_id) {
                url += `?invocation_id=${invocation_id}`;
            }
            this._display_vue_helper(FormGeneric, {
                url: url,
                redirect: "pages/list",
                active_tab: "user",
            });
        },

        show_pages_edit: function () {
            this._display_vue_helper(FormGeneric, {
                url: `page/edit?id=${QueryStringParsing.get("id")}`,
                redirect: "pages/list",
                active_tab: "user",
            });
        },

        show_pages_sharing: function () {
            this._display_vue_helper(Sharing, {
                id: QueryStringParsing.get("id"),
                plural_name: "Pages",
                model_class: "Page",
            });
        },

        show_plugins: function () {
            this._display_vue_helper(PluginList);
        },

        show_workflows: function () {
            this._display_vue_helper(WorkflowList);
        },

        show_workflows_create: function () {
            this._display_vue_helper(FormGeneric, {
                url: "workflow/create",
                redirect: "workflow/editor",
                active_tab: "workflow",
                submitTitle: "Create",
                submitIcon: "fa-check",
                cancelRedirect: "workflows/list",
            });
        },

        show_workflows_run: function () {
            this._loadWorkflow();
        },

        show_workflows_import: function () {
            this._display_vue_helper(WorkflowImport);
        },

        show_workflows_trs_import: function () {
            const queryTrsServer = QueryStringParsing.get("trs_server");
            const queryTrsId = QueryStringParsing.get("trs_id");
            const queryTrsVersionId = QueryStringParsing.get("trs_version");
            const isRun = QueryStringParsing.get("run_form") === "true";
            const propsData = {
                queryTrsServer,
                queryTrsId,
                queryTrsVersionId,
                isRun,
            };
            this._display_vue_helper(TrsImport, propsData);
        },

        show_workflows_trs_search: function () {
            this._display_vue_helper(TrsSearch);
        },

        show_custom_builds: function () {
            const historyPanel = this.page.historyPanel.historyView;
            if (!historyPanel || !historyPanel.model || !historyPanel.model.id) {
                window.setTimeout(() => {
                    this.show_custom_builds();
                }, 500);
                return;
            }
            this._display_vue_helper(CustomBuilds);
        },

        show_dataset_edit_attributes: function (params) {
            const datasetId = params.dataset_id;
            this._display_vue_helper(DatasetAttributes, { datasetId: datasetId });
        },

        show_dataset_error: function (params) {
            const datasetId = params.dataset_id;
            this._display_vue_helper(DatasetError, { datasetId: datasetId });
        },

        /**  */
        home: function (params) {
            // TODO: to router, remove Globals
            // load a tool by id (tool_id) or rerun a previous tool execution (job_id)
            if (params.tool_id || params.job_id) {
                if (params.tool_id === "upload1") {
                    this.page.toolPanel.upload.show();
                    this._loadCenterIframe("welcome");
                } else {
                    this._loadToolForm(params);
                }
            } else {
                // show the workflow run form
                if (params.workflow_id) {
                    this._loadWorkflow();
                    // load the center iframe with controller.action: galaxy.org/?m_c=history&m_a=list -> history/list
                } else if (params.m_c) {
                    this._loadCenterIframe(`${params.m_c}/${params.m_a}`);
                    // show the workflow run form
                } else {
                    this._loadCenterIframe("welcome");
                }
            }
        },

        /** load the center panel with a tool form described by the given params obj */
        _loadToolForm: function (params) {
            //TODO: load tool form code async
            if (params.tool_id) {
                params.id = decodeUriComponent(params.tool_id);
            }
            if (params.version) {
                params.version = decodeUriComponent(params.version);
            }
            this._display_vue_helper(ToolForm, params);
        },

        /** load the center panel iframe using the given url */
        _loadCenterIframe: function (url, root) {
            root = root || getAppRoot();
            url = root + url;
            this.page.$("#galaxy_main").prop("src", url);
        },

        /** load workflow by its url in run mode */
        _loadWorkflow: function () {
            const workflowId = QueryStringParsing.get("id");
            const Galaxy = getGalaxyInstance();
            let preferSimpleForm = Galaxy.config.simplified_workflow_run_ui == "prefer";
            const preferSimpleFormOverride = QueryStringParsing.get("simplified_workflow_run_ui");
            if (preferSimpleFormOverride == "prefer") {
                preferSimpleForm = true;
            }
            const simpleFormTargetHistory = Galaxy.config.simplified_workflow_run_ui_target_history;
            const simpleFormUseJobCache = Galaxy.config.simplified_workflow_run_ui_job_cache == "on";
            const props = {
                workflowId,
                preferSimpleForm,
                simpleFormTargetHistory,
                simpleFormUseJobCache,
            };
            this._display_vue_helper(WorkflowRun, props, "workflow");
        },
    });
};
