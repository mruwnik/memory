import email
from datetime import datetime
from typing import Any


class MockEmailProvider:
    """
    Mock IMAP email provider for integration testing.
    Can be initialized with predefined emails to return.
    """
    
    def __init__(self, emails_by_folder: dict[str, list[dict[str, Any]]] = None):
        """
        Initialize with a dictionary of emails organized by folder.
        
        Args:
            emails_by_folder: A dictionary mapping folder names to lists of email dictionaries.
                Each email dict should have: 'uid', 'flags', 'date', 'from', 'to', 'subject', 
                'message_id', 'body', and optionally 'attachments'.
        """
        self.emails_by_folder = emails_by_folder or {
            "INBOX": [],
            "Sent": [],
            "Archive": []
        }
        self.current_folder = None
        self.is_connected = False
        
    def _generate_email_string(self, email_data: dict[str, Any]) -> str:
        """Generate a raw email string from the provided email data."""
        msg = email.message.EmailMessage()
        msg["From"] = email_data.get("from", "sender@example.com")
        msg["To"] = email_data.get("to", "recipient@example.com")
        msg["Subject"] = email_data.get("subject", "Test Subject")
        msg["Message-ID"] = email_data.get("message_id", f"<test-{email_data['uid']}@example.com>")
        msg["Date"] = email_data.get("date", datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"))
        
        # Set the body content
        msg.set_content(email_data.get("body", f"This is test email body {email_data['uid']}"))
        
        # Add attachments if present
        for attachment in email_data.get("attachments", []):
            if isinstance(attachment, dict) and "filename" in attachment and "content" in attachment:
                msg.add_attachment(
                    attachment["content"],
                    maintype=attachment.get("maintype", "application"),
                    subtype=attachment.get("subtype", "octet-stream"),
                    filename=attachment["filename"]
                )
        
        return msg.as_string()
    
    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        """Mock login method."""
        self.is_connected = True
        return ('OK', [b'Login successful'])
    
    def logout(self) -> tuple[str, list[bytes]]:
        """Mock logout method."""
        self.is_connected = False
        return ('OK', [b'Logout successful'])
    
    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        """
        Select a folder and make it the current active folder.
        
        Args:
            folder: Folder name to select
            readonly: Whether to open in readonly mode
            
        Returns:
            IMAP-style response with message count
        """
        folder_name = folder.decode() if isinstance(folder, bytes) else folder
        self.current_folder = folder_name
        message_count = len(self.emails_by_folder.get(folder_name, []))
        return ('OK', [str(message_count).encode()])
    
    def list(self, directory: str = '', pattern: str = '*') -> tuple[str, list[bytes]]:
        """List available folders."""
        folders = []
        for folder in self.emails_by_folder.keys():
            folders.append(f'(\\HasNoChildren) "/" "{folder}"'.encode())
        return ('OK', folders)
    
    def search(self, charset, criteria):
        """
        Handle SEARCH command to find email UIDs.
        
        Args:
            charset: Character set (ignored in mock)
            criteria: Search criteria (ignored in mock, we return all emails)
            
        Returns:
            All email UIDs in the current folder
        """
        if not self.current_folder or self.current_folder not in self.emails_by_folder:
            return ('OK', [b''])
        
        uids = [str(email["uid"]).encode() for email in self.emails_by_folder[self.current_folder]]
        return ('OK', [b' '.join(uids) if uids else b''])
    
    def fetch(self, message_set, message_parts) -> tuple[str, list]:
        """
        Handle FETCH command to retrieve email data.
        
        Args:
            message_set: Message numbers/UIDs to fetch
            message_parts: Parts of the message to fetch
            
        Returns:
            Email data in IMAP format
        """
        if not self.current_folder or self.current_folder not in self.emails_by_folder:
            return ('OK', [None])
        
        # For simplicity, we'll just match the UID with the ID provided
        uid = int(message_set.decode() if isinstance(message_set, bytes) else message_set)
        
        # Find the email with the matching UID
        for email_data in self.emails_by_folder[self.current_folder]:
            if email_data["uid"] == uid:
                # Generate email content
                email_string = self._generate_email_string(email_data)
                flags = email_data.get("flags", "\\Seen")
                date = email_data.get("date_internal", "01-Jan-2023 00:00:00 +0000")
                
                # Format the response as expected by the IMAP client
                response = [(
                    f'{uid} (UID {uid} FLAGS ({flags}) INTERNALDATE "{date}" RFC822 '
                    f'{{{len(email_string)}}}'.encode(),
                    email_string.encode()
                )]
                return ('OK', response)
        
        # No matching email found
        return ('NO', [b'Email not found'])