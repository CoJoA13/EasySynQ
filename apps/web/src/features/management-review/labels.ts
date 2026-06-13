import type { ReviewInputType, ReviewOutputType } from "../../lib/types";

export const INPUT_LABEL: Record<ReviewInputType, string> = {
  PRIOR_ACTIONS: "Status of actions from prior reviews",
  CONTEXT_CHANGES: "Changes in context & interested parties",
  CUSTOMER_SATISFACTION: "Customer satisfaction & feedback",
  OBJECTIVES_STATUS: "Quality objectives status",
  PROCESS_PERFORMANCE: "Process performance & conformity",
  NONCONFORMITIES_CAPA: "Nonconformities & corrective actions",
  MONITORING_RESULTS: "Monitoring & measurement results",
  AUDIT_RESULTS: "Audit results",
  SUPPLIER_PERFORMANCE: "External provider performance",
  RESOURCE_ADEQUACY: "Adequacy of resources",
  RISK_OPPORTUNITY_ACTIONS: "Effectiveness of actions on risks & opportunities",
  IMPROVEMENT_OPPORTUNITIES: "Opportunities for improvement",
};

export const OUTPUT_LABEL: Record<ReviewOutputType, string> = {
  DECISION: "Decision",
  ACTION: "Action",
  IMPROVEMENT: "Improvement opportunity",
};
