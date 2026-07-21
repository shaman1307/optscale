import Button from "components/Button";
import ButtonLoader from "components/ButtonLoader";
import FormButtonsWrapper from "components/FormButtonsWrapper";
import { FormButtonsProps } from "../types";

const FormButtons = ({ onCancel, isLoading = false }: FormButtonsProps) => (
  <FormButtonsWrapper>
    <ButtonLoader
      messageId="save"
      variant="contained"
      color="primary"
      type="submit"
      isLoading={isLoading}
      dataTestId="btn_save_snowflake_cost_model"
    />
    <Button messageId="cancel" onClick={onCancel} dataTestId="btn_cancel_snowflake_cost_model" />
  </FormButtonsWrapper>
);

export default FormButtons;
