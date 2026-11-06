from django.core.mail.backends.console import EmailBackend


class UnicodeSafeConsoleEmailBackend(EmailBackend):
    def write_message(self, message):
        email = message.message()
        message_data = email.as_bytes()
        charset = (
            email.get_charset().get_output_charset()
            if email.get_charset()
            else "utf-8"
        )
        text = message_data.decode(charset)
        stream_encoding = getattr(self.stream, "encoding", None) or "utf-8"
        text = text.encode(
            stream_encoding,
            errors="backslashreplace",
        ).decode(stream_encoding)
        self.stream.write(f"{text}\n")
        self.stream.write("-" * 79)
        self.stream.write("\n")
