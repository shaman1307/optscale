import { useIntl } from "react-intl";
import DataSourceBillingReimportForm from "components/forms/DataSourceBillingReimportForm/DataSourceBillingReimportForm";
import {
  DataSourceDocument,
  useReportImportsLazyQuery,
  useUpdateDataSourceMutation,
} from "graphql/__generated__/hooks/restapi";
import { getStartOfDayInUTCinSeconds } from "utils/datetime";

const ACTIVE_IMPORT_STATES = new Set(["scheduled", "in_progress"]);

type DataSourceBillingReimportContainerProps = {
  dataSourceId: string;
  onSuccess: () => void;
};

const DataSourceBillingReimportContainer = ({ dataSourceId, onSuccess }: DataSourceBillingReimportContainerProps) => {
  const intl = useIntl();
  const [updateDataSource, { loading }] = useUpdateDataSourceMutation();
  const [fetchReportImports] = useReportImportsLazyQuery();

  return (
    <DataSourceBillingReimportForm
      onSubmit={async (formData) => {
        const { data: importsData } = await fetchReportImports({
          variables: {
            cloudAccountId: dataSourceId,
            showCompleted: true,
          },
          fetchPolicy: "network-only",
        });
        const isImportInProgress = (importsData?.reportImports ?? []).some((item) =>
          ACTIVE_IMPORT_STATES.has(item.state)
        );
        if (isImportInProgress) {
          throw new Error(intl.formatMessage({ id: "billingImportAlreadyInProgress" }));
        }

        const importFrom = getStartOfDayInUTCinSeconds(formData.importFrom);

        return updateDataSource({
          variables: {
            dataSourceId,
            params: {
              lastImportAt: importFrom,
              lastImportModifiedAt: importFrom,
            },
          },
          refetchQueries: [DataSourceDocument],
        }).then(onSuccess);
      }}
      isSubmitLoading={loading}
    />
  );
};

export default DataSourceBillingReimportContainer;
