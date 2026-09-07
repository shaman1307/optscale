import { ApolloClient, ApolloLink, ApolloProvider, InMemoryCache } from "@apollo/client";
import { IntlProvider } from "react-intl";
import { Provider } from "react-redux";
import { MemoryRouter } from "react-router-dom";
import createMockStore from "redux-mock-store";
import ThemeProviderWrapper from "components/ThemeProviderWrapper";
import apiMiddleware from "middleware/api";
import intlConfig from "translations/react-intl-config";

const mockStore = createMockStore([apiMiddleware]);

const TestProvider = ({ children, state = {} }) => {
  const store = mockStore(state);
  const apolloClient = new ApolloClient({
    cache: new InMemoryCache(),
    link: ApolloLink.empty(),
  });

  return (
    <ApolloProvider client={apolloClient}>
      <Provider store={store}>
        <ThemeProviderWrapper>
          <IntlProvider {...intlConfig}>
            <MemoryRouter>{children}</MemoryRouter>
          </IntlProvider>
        </ThemeProviderWrapper>
      </Provider>
    </ApolloProvider>
  );
};

export default TestProvider;
