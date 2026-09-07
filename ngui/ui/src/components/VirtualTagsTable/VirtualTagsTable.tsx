import { useMemo } from "react";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import ContentCopyOutlinedIcon from "@mui/icons-material/ContentCopyOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import RepeatOutlinedIcon from "@mui/icons-material/RepeatOutlined";
import { FormattedMessage } from "react-intl";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import Link from "@mui/material/Link";
import { CopyVirtualTagsModal, DeleteVirtualTagModal, ReapplyVirtualTagsModal } from "components/SideModalManager/SideModals";
import Table from "components/Table";
import TableCellActions from "components/TableCellActions";
import TableLoader from "components/TableLoader";
import TextWithDataTestId from "components/TextWithDataTestId";
import { useIsAllowed } from "hooks/useAllowedActions";
import { useOpenSideModal } from "hooks/useOpenSideModal";
import { VIRTUAL_TAG_CREATE, getEditVirtualTagUrl, getVirtualTagUrl } from "urls";

const VirtualTagsTable = ({ virtualTags, isLoading = false, quarter }) => {
  const openSideModal = useOpenSideModal();
  const navigate = useNavigate();
  const isManageAllowed = useIsAllowed({ requiredActions: ["EDIT_PARTNER"] });

  const columns = useMemo(
    () => [
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_key">
            <FormattedMessage id="key" />
          </TextWithDataTestId>
        ),
        accessorKey: "key",
        cell: ({ row: { original } }) => (
          <Link to={getVirtualTagUrl(original.id)} component={RouterLink}>
            {original.key}
          </Link>
        ),
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_name">
            <FormattedMessage id="name" />
          </TextWithDataTestId>
        ),
        accessorKey: "name",
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_quarter">
            <FormattedMessage id="quarter" />
          </TextWithDataTestId>
        ),
        accessorKey: "quarter",
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_mode">
            <FormattedMessage id="virtualTagMode" />
          </TextWithDataTestId>
        ),
        accessorKey: "mode",
        cell: ({ row: { original } }) => (
          <FormattedMessage
            id={original.mode === "extract" ? "virtualTagModeExtract" : "virtualTagModeAssignment"}
          />
        ),
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_actions">
            <FormattedMessage id="actions" />
          </TextWithDataTestId>
        ),
        id: "actions",
        enableSorting: false,
        cell: ({ row: { original } }) =>
          isManageAllowed ? (
            <TableCellActions
              items={[
                {
                  key: "edit",
                  messageId: "edit",
                  icon: <EditOutlinedIcon />,
                  action: () => navigate(getEditVirtualTagUrl(original.id)),
                },
                {
                  key: "delete",
                  messageId: "delete",
                  icon: <DeleteOutlinedIcon />,
                  action: () =>
                    openSideModal(DeleteVirtualTagModal, {
                      virtualTagId: original.id,
                      virtualTagName: original.name || original.key,
                    }),
                  color: "error",
                },
              ]}
            />
          ) : null,
      },
    ],
    [isManageAllowed, navigate, openSideModal]
  );

  return isLoading ? (
    <TableLoader columnsCounter={5} />
  ) : (
    <Table
      data={virtualTags}
      columns={columns}
      withSearch
      actionBar={{
        show: true,
        definition: {
          items: [
            {
              key: "add",
              icon: <AddOutlinedIcon fontSize="small" />,
              messageId: "add",
              type: "button",
              variant: "contained",
              link: VIRTUAL_TAG_CREATE,
              requiredActions: ["EDIT_PARTNER"],
              dataTestId: "btn_add_virtual_tag",
            },
            {
              key: "copy",
              icon: <ContentCopyOutlinedIcon fontSize="small" />,
              messageId: "copyVirtualTagsToQuarter",
              type: "button",
              action: () => openSideModal(CopyVirtualTagsModal, { sourceQuarter: quarter }),
              requiredActions: ["EDIT_PARTNER"],
            },
            {
              key: "reapply",
              icon: <RepeatOutlinedIcon fontSize="small" />,
              messageId: "reapplyVirtualTags",
              type: "button",
              action: () => openSideModal(ReapplyVirtualTagsModal, { quarter }),
              requiredActions: ["EDIT_PARTNER"],
            },
          ],
        },
      }}
      localization={{ emptyMessageId: "noVirtualTags" }}
    />
  );
};

export default VirtualTagsTable;
