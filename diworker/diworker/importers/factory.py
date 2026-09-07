def get_importer_class(cloud_type, import_scheme):
    """Resolve importer class with lazy imports.

    Eagerly importing every cloud adapter (especially snowflake-connector)
    loads cffi into the process even for GCP-only work. Lazy imports keep
    native bindings out of processes that do not need them.
    """
    if cloud_type == 'aws_cnr' and import_scheme is None:
        from diworker.diworker.importers.aws import AWSReportImporter
        return AWSReportImporter
    if cloud_type == 'azure_cnr':
        if import_scheme in (None, 'usage', 'raw_usage', 'partner_raw_usage'):
            from diworker.diworker.importers.azure import AzureApiImporter
            return AzureApiImporter
        if import_scheme == 'export':
            from diworker.diworker.importers.azure_export import (
                AzureExportImporter)
            return AzureExportImporter
    if cloud_type == 'kubernetes_cnr' and import_scheme is None:
        from diworker.diworker.importers.kubernetes import (
            KubernetesReportImporter)
        return KubernetesReportImporter
    if cloud_type == 'alibaba_cnr' and import_scheme is None:
        from diworker.diworker.importers.alibaba import AlibabaReportImporter
        return AlibabaReportImporter
    if cloud_type == 'gcp_cnr' and import_scheme is None:
        from diworker.diworker.importers.gcp import GcpReportImporter
        return GcpReportImporter
    if cloud_type == 'nebius' and import_scheme is None:
        from diworker.diworker.importers.nebius import NebiusReportImporter
        return NebiusReportImporter
    if cloud_type == 'environment' and import_scheme is None:
        from diworker.diworker.importers.environment import (
            EnvironmentReportImporter)
        return EnvironmentReportImporter
    if cloud_type == 'databricks' and import_scheme is None:
        from diworker.diworker.importers.databricks import (
            DatabricksReportImporter)
        return DatabricksReportImporter
    if cloud_type in ('snowflake', 'snowflake_tenant') and import_scheme is None:
        from diworker.diworker.importers.snowflake import (
            SnowflakeReportImporter)
        return SnowflakeReportImporter

    cloud_types = {
        'aws_cnr', 'azure_cnr', 'kubernetes_cnr', 'alibaba_cnr', 'gcp_cnr',
        'nebius', 'environment', 'databricks', 'snowflake', 'snowflake_tenant',
    }
    if cloud_type not in cloud_types:
        raise ValueError('Cloud {} is not supported'.format(cloud_type))
    raise ValueError('Expense import scheme {} is not supported'.format(
        import_scheme))
