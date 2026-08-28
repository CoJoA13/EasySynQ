import { Component, Fragment, type ReactNode } from "react";

export interface ApplicationErrorFallbackProps {
  onReset: () => void;
  // U15 follow-on: the fallback needs the error to tell a RECOVERABLE render fault from a failed
  // dynamic import. React.lazy memoizes a rejected payload, so remounting can never re-fetch a
  // stale chunk — only a full reload can.
  error: unknown;
}

export interface ApplicationErrorBoundaryProps {
  children: ReactNode;
  fallback: (props: ApplicationErrorFallbackProps) => ReactNode;
  resetKey?: string;
}

interface ApplicationErrorBoundaryState {
  failed: boolean;
  error: unknown;
  observedResetKey: string | undefined;
  retryEpoch: number;
}

export class ApplicationErrorBoundary extends Component<
  ApplicationErrorBoundaryProps,
  ApplicationErrorBoundaryState
> {
  state: ApplicationErrorBoundaryState = {
    failed: false,
    error: undefined,
    observedResetKey: this.props.resetKey,
    retryEpoch: 0,
  };

  static getDerivedStateFromError(
    error: unknown,
  ): Pick<ApplicationErrorBoundaryState, "failed" | "error"> {
    return { failed: true, error };
  }

  static getDerivedStateFromProps(
    props: ApplicationErrorBoundaryProps,
    state: ApplicationErrorBoundaryState,
  ): Partial<ApplicationErrorBoundaryState> | null {
    if (props.resetKey === state.observedResetKey) return null;
    return {
      failed: false,
      error: undefined,
      observedResetKey: props.resetKey,
      retryEpoch: state.failed ? state.retryEpoch + 1 : state.retryEpoch,
    };
  }

  private readonly reset = (): void => {
    this.setState((state) => ({
      failed: false,
      error: undefined,
      observedResetKey: this.props.resetKey,
      retryEpoch: state.retryEpoch + 1,
    }));
  };

  render(): ReactNode {
    if (this.state.failed)
      return this.props.fallback({ onReset: this.reset, error: this.state.error });
    return <Fragment key={this.state.retryEpoch}>{this.props.children}</Fragment>;
  }
}
