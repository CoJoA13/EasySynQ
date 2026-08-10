import { Component, Fragment, type ReactNode } from "react";

export interface ApplicationErrorFallbackProps {
  onReset: () => void;
}

export interface ApplicationErrorBoundaryProps {
  children: ReactNode;
  fallback: (props: ApplicationErrorFallbackProps) => ReactNode;
  resetKey?: string;
}

interface ApplicationErrorBoundaryState {
  failed: boolean;
  observedResetKey: string | undefined;
  retryEpoch: number;
}

export class ApplicationErrorBoundary extends Component<
  ApplicationErrorBoundaryProps,
  ApplicationErrorBoundaryState
> {
  state: ApplicationErrorBoundaryState = {
    failed: false,
    observedResetKey: this.props.resetKey,
    retryEpoch: 0,
  };

  static getDerivedStateFromError(): Pick<ApplicationErrorBoundaryState, "failed"> {
    return { failed: true };
  }

  static getDerivedStateFromProps(
    props: ApplicationErrorBoundaryProps,
    state: ApplicationErrorBoundaryState,
  ): Partial<ApplicationErrorBoundaryState> | null {
    if (props.resetKey === state.observedResetKey) return null;
    return {
      failed: false,
      observedResetKey: props.resetKey,
      retryEpoch: state.failed ? state.retryEpoch + 1 : state.retryEpoch,
    };
  }

  private readonly reset = (): void => {
    this.setState((state) => ({
      failed: false,
      observedResetKey: this.props.resetKey,
      retryEpoch: state.retryEpoch + 1,
    }));
  };

  render(): ReactNode {
    if (this.state.failed) return this.props.fallback({ onReset: this.reset });
    return <Fragment key={this.state.retryEpoch}>{this.props.children}</Fragment>;
  }
}
