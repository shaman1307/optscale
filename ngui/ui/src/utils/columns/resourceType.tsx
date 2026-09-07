import { FormattedMessage } from "react-intl";
import TextWithDataTestId from "components/TextWithDataTestId";

const RESOURCE_TYPE_MESSAGE_IDS: Record<string, string> = {
  instance: "resourceType.instance",
  volume: "resourceType.volume",
  snapshot: "resourceType.snapshot",
  bucket: "resourceType.bucket",
  k8s_pod: "resourceType.k8sPod",
  snapshot_chain: "resourceType.snapshotChain",
  rds_instance: "resourceType.rdsInstance",
  ip_address: "resourceType.ipAddress",
  image: "resourceType.image",
  load_balancer: "resourceType.loadBalancer",
};

const RESOURCE_TYPE_VALUE_TO_NAME: Record<string, string> = {
  Instance: "instance",
  Volume: "volume",
  Snapshot: "snapshot",
  Bucket: "bucket",
  "K8s Pod": "k8s_pod",
  "Snapshot Chain": "snapshot_chain",
  "RDS Instance": "rds_instance",
  "IP Address": "ip_address",
  Image: "image",
  "Load Balancer": "load_balancer",
};

const getMessageId = (resourceType: unknown) => {
  if (typeof resourceType !== "string") {
    return undefined;
  }
  return RESOURCE_TYPE_MESSAGE_IDS[resourceType] || RESOURCE_TYPE_MESSAGE_IDS[RESOURCE_TYPE_VALUE_TO_NAME[resourceType]];
};

const resourceType = ({
  headerDataTestId = "lbl_resource_type",
  messageId = "resourceType",
  accessorKey = "resource_type",
  style = {},
} = {}) => ({
  header: (
    <TextWithDataTestId dataTestId={headerDataTestId}>
      <FormattedMessage id={messageId} />
    </TextWithDataTestId>
  ),
  accessorKey,
  style,
  cell: ({ cell }) => {
    const type = cell.getValue();

    const typeTranslationMessageId = getMessageId(type);

    return typeTranslationMessageId ? <FormattedMessage id={typeTranslationMessageId} /> : type;
  },
});

export default resourceType;
