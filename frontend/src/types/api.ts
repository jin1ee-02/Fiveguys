// Integration contract matching backend/app/schemas.py.
export type Speaker = 'A' | 'B';

export interface ChatTurn {
  speaker: Speaker;
  text: string;
}

export interface ConversationInput {
  recent_messages: ChatTurn[];
  speaker: Speaker;
  text: string;
  relationship: string;
  summary?: string | null;
  previous_temperature: number;
  // In a room the server attaches that room's Core State to the analysis (the request must carry the room token).
  room_id?: string | null;
}

export interface FeatureScores {
  hostility: number;
  sarcasm: number;
  blame: number;
  repair: number;
  escalation_delta: number;
  emotion?: number | null;
  confidence: number;
  rationale: string;
  model_version?: string | null;
}

export interface MessageResult {
  features: FeatureScores;
  raw_conflict: number;
  previous_temperature: number;
  temperature: number;
}

export interface ReceiptResult {
  status: 'received';
  received: ConversationInput;
}

export type DeliveryResult =
  | { mode: 'local' }
  | { mode: 'receipt'; data: ReceiptResult }
  | { mode: 'ai'; data: MessageResult };

export interface Message extends ChatTurn {
  id: string;
  createdAt: string;
  result: DeliveryResult;
}
