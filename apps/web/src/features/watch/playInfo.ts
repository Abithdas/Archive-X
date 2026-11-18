/** What the server knows about a video's container and codecs (`/api/play/info`), and its verdict on a typical browser. */
export interface PlayInfo { direct_likely?: boolean; container_ok?: boolean; video_ok?: boolean; audio_ok?: boolean; ten_bit?: boolean; name?: string; format?: string; mime?: string; vcodec?: string; acodec?: string }
