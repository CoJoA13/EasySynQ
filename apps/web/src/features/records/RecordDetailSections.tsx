import { Anchor, Card, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { humanizeToken } from "../../lib/labels";
import { formatTimestamp } from "../../lib/time";
import type { RecordDetail, RecordEvidenceLink } from "../../lib/types";
import { RecordDownloadButton } from "./RecordDownloadButton";

function valueOrDash(value: ReactNode | null | undefined): ReactNode {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function DetailList({ children }: { children: ReactNode }) {
  return (
    <Stack component="dl" gap="xs" m={0}>
      {children}
    </Stack>
  );
}

function DetailValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text component="dt" size="xs" c="dimmed" fw={600}>
        {label}
      </Text>
      <Text component="dd" m={0} style={{ overflowWrap: "anywhere" }}>
        {valueOrDash(children)}
      </Text>
    </div>
  );
}

function sectionId(recordId: string, name: string): string {
  return `record-${recordId}-${name}`;
}

function Section({
  recordId,
  name,
  title,
  children,
}: {
  recordId: string;
  name: string;
  title: string;
  children: ReactNode;
}) {
  const id = sectionId(recordId, name);
  return (
    <Card component="section" withBorder aria-labelledby={id}>
      <Title order={3} id={id} mb="sm">
        {title}
      </Title>
      {children}
    </Card>
  );
}

function sourceLabel(record: RecordDetail): string {
  const identity = record.source_document_identifier ?? "Source document";
  const title = record.source_document_title ? ` — ${record.source_document_title}` : "";
  const version = record.source_version_label ? ` (${record.source_version_label})` : "";
  return `${identity}${title}${version}`;
}

function RelatedRecord({
  id,
  readable,
  label,
}: {
  id: string | null;
  readable: boolean;
  label: string;
}) {
  if (!id) return <>None recorded</>;
  if (!readable) return <>Restricted related item</>;
  return (
    <Anchor component={Link} to={`/records/${id}`}>
      {label}
    </Anchor>
  );
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Number((size / 1024).toFixed(1))} KB`;
  return `${Number((size / (1024 * 1024)).toFixed(1))} MB`;
}

function renderPrimitive(value: unknown): ReactNode {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function StructuredValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <Stack component="ul" gap={4} my={0} pl="lg">
        {value.map((item, index) => (
          <li key={index}>
            <StructuredValue value={item} />
          </li>
        ))}
      </Stack>
    );
  }
  if (value !== null && typeof value === "object") {
    return (
      <Stack component="dl" gap="xs" m={0}>
        {Object.entries(value).map(([key, nested]) => (
          <div key={key}>
            <Text component="dt" fw={600} size="sm">
              {humanizeToken(key)}
            </Text>
            <Text component="dd" m={0} style={{ overflowWrap: "anywhere" }}>
              <StructuredValue value={nested} />
            </Text>
          </div>
        ))}
      </Stack>
    );
  }
  return <>{renderPrimitive(value)}</>;
}

function evidenceTarget(link: RecordEvidenceLink): ReactNode {
  if (!link.target_readable || !link.target_label) return "Restricted related item";
  const label = link.target_label;
  if (link.target_type !== "document") return label;
  return (
    <Anchor component={Link} to={`/documents/${link.target_id}`}>
      {label}
    </Anchor>
  );
}

export function RecordDetailSections({ record }: { record: RecordDetail }) {
  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Section recordId={record.id} name="provenance" title="Provenance">
          <DetailList>
            <DetailValue label="Captured">
              {record.captured_at ? formatTimestamp(record.captured_at) : null}
            </DetailValue>
            <DetailValue label="Captured by">
              {record.captured_by_display_name ?? "Restricted related item"}
            </DetailValue>
            <DetailValue label="Source document">
              {!record.source_document_id ? (
                "None recorded"
              ) : record.source_document_readable ? (
                <Anchor component={Link} to={`/documents/${record.source_document_id}`}>
                  {sourceLabel(record)}
                </Anchor>
              ) : (
                "Restricted related item"
              )}
            </DetailValue>
            <DetailValue label="Framework">{record.framework_id}</DetailValue>
            <DetailValue label="Seal">Seal version {record.content_hash_version}</DetailValue>
            <DetailValue label="Seal hash">{record.content_hash}</DetailValue>
          </DetailList>
        </Section>

        <Section recordId={record.id} name="lifecycle" title="Lifecycle">
          <DetailList>
            <DetailValue label="Retention policy">
              {record.retention_policy_name ?? "Restricted related item"}
            </DetailValue>
            <DetailValue label="Retention basis date">{record.retention_basis_date}</DetailValue>
            <DetailValue label="Disposition">{humanizeToken(record.disposition_state)}</DetailValue>
            <DetailValue label="Legal hold">{record.legal_hold ? "Yes" : "No"}</DetailValue>
            <DetailValue label="Correction of">
              <RelatedRecord
                id={record.correction_of}
                readable={record.correction_of_readable}
                label="Previous record"
              />
            </DetailValue>
            <DetailValue label="Superseded by correction">
              <RelatedRecord
                id={record.superseded_by_correction}
                readable={record.superseded_by_correction_readable}
                label="Correction record"
              />
            </DetailValue>
          </DetailList>
        </Section>
      </SimpleGrid>

      {record.evidence_blobs.length > 0 && (
        <Section recordId={record.id} name="evidence" title="Evidence files">
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            {record.evidence_blobs.map((blob) => (
              <Card key={blob.sha256} withBorder>
                <DetailList>
                  <DetailValue label="Filename">{blob.filename}</DetailValue>
                  <DetailValue label="Content type">{blob.content_type}</DetailValue>
                  <DetailValue label="Size">{formatBytes(blob.size_bytes)}</DetailValue>
                  <DetailValue label="SHA-256">{blob.sha256}</DetailValue>
                  <DetailValue label="Evidence kind">
                    {blob.is_original ? "Original" : "Derived"}
                  </DetailValue>
                  <DetailValue label="Attached">
                    {blob.created_at ? formatTimestamp(blob.created_at) : null}
                  </DetailValue>
                </DetailList>
                <RecordDownloadButton
                  label={`Download ${blob.filename ?? "evidence"}`}
                  endpoint={`/api/v1/records/${record.id}/evidence/${blob.sha256}/download`}
                />
              </Card>
            ))}
          </SimpleGrid>
        </Section>
      )}

      {record.form_field_values && Object.keys(record.form_field_values).length > 0 && (
        <Section recordId={record.id} name="structured-values" title="Structured values">
          <StructuredValue value={record.form_field_values} />
        </Section>
      )}

      {record.evidence_links.length > 0 && (
        <Section recordId={record.id} name="evidence-for" title="Evidence for">
          <Stack gap="sm">
            {record.evidence_links.map((link) => (
              <Card key={link.id} withBorder>
                <DetailList>
                  <DetailValue label="Type">{humanizeToken(link.target_type)}</DetailValue>
                  <DetailValue label="Related item">{evidenceTarget(link)}</DetailValue>
                  <DetailValue label="Reason">{link.link_reason ?? "None recorded"}</DetailValue>
                  <DetailValue label="Linked">
                    {link.created_at ? formatTimestamp(link.created_at) : null}
                  </DetailValue>
                </DetailList>
              </Card>
            ))}
          </Stack>
        </Section>
      )}
    </Stack>
  );
}
