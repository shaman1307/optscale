import { useMemo } from "react";
import { Box } from "@mui/material";
import { FormattedMessage } from "react-intl";
import Button from "components/Button";
import FormButtonsWrapper from "components/FormButtonsWrapper";
import Table from "components/Table";
import TableLoader from "components/TableLoader";
import { useResourceDuplicatesQuery } from "graphql/__generated__/hooks/restapi";
import { isEmptyArray } from "utils/arrays";
import BaseSideModal from "./BaseSideModal";

const ResourceDuplicatesContent = ({ dataSourceId, onClose }) => {
  const { data, loading, error } = useResourceDuplicatesQuery({
    variables: { cloudAccountId: dataSourceId },
    skip: !dataSourceId,
  });

  const columns = useMemo(
    () => [
      {
        header: <FormattedMessage id="dataSource" />,
        id: "cloud_account_name",
        accessorFn: (row) => row.cloud_account_name || row.cloud_account_id,
        style: { minWidth: "140px" },
      },
      {
        header: <FormattedMessage id="cloudResourceId" />,
        accessorKey: "cloud_resource_id",
        style: { minWidth: "220px" },
      },
      {
        header: <FormattedMessage id="count" />,
        accessorKey: "count",
        style: { minWidth: "80px" },
      },
      {
        header: <FormattedMessage id="resources" />,
        id: "resource_names",
        accessorFn: (row) => (row.resources || []).map((resource) => resource.name || resource.id).join(", "),
        style: { minWidth: "180px" },
      },
    ],
    []
  );

  const tableData = data?.resourceDuplicates?.duplicate_groups ?? [];

  let body;
  if (loading) {
    body = <TableLoader columnsCounter={columns.length} />;
  } else if (error) {
    body = (
      <Box data-test-id="resource_duplicates_error">
        <FormattedMessage id="failedToLoadResourceDuplicates" />
      </Box>
    );
  } else {
    body = (
      <Table
        data={tableData}
        columns={columns}
        localization={{ emptyMessageId: "noResourceDuplicates" }}
        counters={{ show: !isEmptyArray(tableData) }}
      />
    );
  }

  return (
    <>
      {body}
      <Box mt={2}>
        <FormButtonsWrapper>
          <Button messageId="close" onClick={onClose} />
        </FormButtonsWrapper>
      </Box>
    </>
  );
};

class ResourceDuplicatesModal extends BaseSideModal {
  headerProps = {
    messageId: "resourceDuplicates",
    dataTestIds: {
      title: "lbl_resource_duplicates",
      closeButton: "btn_close",
    },
  };

  dataTestId = "smodal_resource_duplicates";

  get content() {
    return <ResourceDuplicatesContent dataSourceId={this.payload?.dataSourceId} onClose={this.closeSideModal} />;
  }
}

export default ResourceDuplicatesModal;
