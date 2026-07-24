"use client";

import { useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { WorkflowGraph } from "@/lib/types";
import { normalisePositions } from "@/lib/graph";

const TYPE_LABEL: Record<string, string> = {
  startCall: "start",
  agentNode: "agent",
  endCall: "end",
  globalNode: "global",
};

function WorkflowNodeView({ data }: NodeProps) {
  const d = data as { name: string; kind: string; mutated: boolean };
  return (
    <div className={`rf-node ${d.mutated ? "rf-node-mutated" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="rf-node-name">{d.name}</div>
      <div className="rf-node-role">{d.kind}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { workflow: WorkflowNodeView };

export function GraphPanel({
  graph,
  mutatedNodeId,
  changedEdgeId,
  heading,
  subheading,
  accent = false,
}: {
  graph: WorkflowGraph;
  mutatedNodeId?: string | null;
  changedEdgeId?: string | null;
  heading: string;
  subheading: string;
  accent?: boolean;
}) {
  const { nodes, edges } = useMemo(() => {
    const g = normalisePositions(graph);
    const rfNodes: Node[] = g.nodes.map((n) => ({
      id: n.id,
      type: "workflow",
      position: n.position ?? { x: 0, y: 0 },
      draggable: false,
      selectable: false,
      data: {
        name: (n.data?.name as string) ?? n.id,
        kind: TYPE_LABEL[n.type ?? ""] ?? n.type ?? "node",
        mutated: !!mutatedNodeId && n.id === mutatedNodeId,
      },
    }));
    const rfEdges: Edge[] = g.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      animated: !!changedEdgeId && e.id === changedEdgeId,
      className: changedEdgeId && e.id === changedEdgeId ? "rf-edge-changed" : undefined,
      style: { strokeWidth: 1.5 },
    }));
    return { nodes: rfNodes, edges: rfEdges };
  }, [graph, mutatedNodeId, changedEdgeId]);

  return (
    <div className="card flex min-w-0 flex-1 flex-col overflow-hidden">
      <div
        className="flex items-baseline justify-between px-5 py-4"
        style={{ borderBottom: "1px solid var(--line-subtle)" }}
      >
        <div className="flex items-baseline gap-3">
          <span
            className="mono"
            style={{
              fontSize: 15,
              fontWeight: 500,
              letterSpacing: "0.5px",
              color: accent ? "var(--color-add)" : "var(--color-ink)",
            }}
          >
            {heading}
          </span>
          <span style={{ fontSize: 14, color: "var(--color-muted)" }}>{subheading}</span>
        </div>
        {accent && (
          <span
            className="pill"
            style={{
              background: "rgba(143,217,168,0.12)",
              color: "var(--color-add)",
              fontSize: 13,
              padding: "3px 12px",
            }}
          >
            mutated
          </span>
        )}
      </div>
      <div className="rf-shell" style={{ height: 330 }}>
        {nodes.length === 0 ? (
          <div
            className="flex h-full items-center justify-center"
            style={{ fontSize: 15, color: "var(--color-muted)" }}
          >
            no graph loaded
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.08, maxZoom: 1.05 }}
            minZoom={0.15}
            maxZoom={1.4}
            zoomOnScroll={false}
            zoomOnDoubleClick={false}
            panOnScroll={false}
            preventScrolling={false}
            nodesConnectable={false}
            elementsSelectable={false}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={26} size={1} color="#242424" />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
