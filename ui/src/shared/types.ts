export type Card = {
  id: string;
  status: string;
  revision?: string | number;
  ocr_direction?: string;
  thumbnail_path?: string;
  back_original_image_path?: string;
  back_processed_image_path?: string;
  back_thumbnail_path?: string;
  back_ocr_direction?: string;
  back_ocr_text?: string;
  back_ocr_duration_ms?: number;
  person_name?: string;
  person_name_kana?: string;
  company_name?: string;
  department?: string;
  title?: string;
  postal_code?: string;
  address?: string;
  tel?: string;
  mobile?: string;
  fax?: string;
  email?: string;
  website?: string;
  tags?: string;
  memo?: string;
  ocr_text?: string;
  extracted_json?: string;
  error_message?: string;
  ocr_duration_ms?: number;
  extraction_duration_ms?: number;
  created_at: string;
  updated_at: string;
};

export type Contact = Card & {
  representative_card_id: string;
  card_count: number;
  cards?: Card[];
  revision?: string;
  has_in_progress?: boolean;
};

export type ContactPage = {
  items: Contact[];
  next_cursor?: string | null;
  revision?: string;
  has_in_progress?: boolean;
  unchanged?: boolean;
};

export type RuntimeVersions = {
  api?: {
    version?: string;
  };
  llm: {
    provider?: string;
    model?: string;
    status?: string;
    server_version?: string | null;
  };
  kana?: {
    model?: string;
    status?: string;
  };
};
