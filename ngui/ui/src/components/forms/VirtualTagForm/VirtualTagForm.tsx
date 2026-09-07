import { useForm, FormProvider } from "react-hook-form";
import { FormattedMessage } from "react-intl";
import { Box } from "@mui/material";
import Link from "@mui/material/Link";
import { Link as RouterLink } from "react-router-dom";
import ActionBar from "components/ActionBar";
import Button from "components/Button";
import ButtonLoader from "components/ButtonLoader";
import FormButtonsWrapper from "components/FormButtonsWrapper";
import PageContentWrapper from "components/PageContentWrapper";
import { RadioGroup, TextInput, Selector } from "components/forms/common/fields";
import { ItemContent } from "components/Selector";
import { VIRTUAL_TAGS } from "urls";
import { SPACING_1 } from "utils/layouts";

const VirtualTagForm = ({
  defaultValues,
  onSubmit,
  onCancel,
  isEdit = false,
  isLoading = false,
  quarters = [],
}) => {
  const methods = useForm({ defaultValues });
  const { handleSubmit, watch } = methods;
  const mode = watch("mode");
  const quarterItems = Array.from(
    new Set([defaultValues.quarter, ...quarters].filter(Boolean))
  ).sort();

  return (
    <>
      <ActionBar
        data={{
          breadcrumbs: [
            <Link key={1} to={VIRTUAL_TAGS} component={RouterLink}>
              <FormattedMessage id="virtualTags" />
            </Link>,
          ],
          title: {
            text: <FormattedMessage id={isEdit ? "editVirtualTag" : "addVirtualTagTitle"} />,
          },
        }}
      />
      <PageContentWrapper>
        <Box sx={{ width: { md: "50%" }, mb: SPACING_1 }}>
          <FormProvider {...methods}>
            <form onSubmit={handleSubmit(onSubmit)} noValidate>
              <TextInput name="key" label={<FormattedMessage id="virtualTagKey" />} required dataTestId="input_key" />
              <TextInput name="name" label={<FormattedMessage id="name" />} required dataTestId="input_name" />
              <Selector
                name="quarter"
                labelMessageId="quarter"
                required
                disabled={isEdit}
                fullWidth
                id="selector-quarter"
                items={quarterItems.map((quarter) => ({
                  value: quarter,
                  content: <ItemContent>{quarter}</ItemContent>,
                }))}
              />
              <RadioGroup
                name="mode"
                labelMessageId="virtualTagMode"
                radioButtons={[
                  { value: "assignment", label: <FormattedMessage id="virtualTagModeAssignment" /> },
                  { value: "extract", label: <FormattedMessage id="virtualTagModeExtract" /> },
                ]}
                row
              />
              {mode === "extract" && (
                <TextInput
                  name="source_tag_key"
                  label={<FormattedMessage id="sourceTagKey" />}
                  required
                  dataTestId="input_source_tag_key"
                />
              )}
              <FormButtonsWrapper>
                <ButtonLoader
                  messageId={isEdit ? "save" : "create"}
                  dataTestId="btn_create"
                  color="primary"
                  variant="contained"
                  type="submit"
                  isLoading={isLoading}
                />
                <Button messageId="cancel" dataTestId="btn_cancel" onClick={onCancel} />
              </FormButtonsWrapper>
            </form>
          </FormProvider>
        </Box>
      </PageContentWrapper>
    </>
  );
};

export default VirtualTagForm;
