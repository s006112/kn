<?php
if (isset($_POST["webPageInput"])) {
    $text = $_POST["webPageInput"];

    // 1. Strictly capture the first line as title (up to first line break)
    $lines = preg_split('/\r?\n/', $text, 2);
    $title = isset($lines[0]) ? trim($lines[0]) : '';

    // 2. Clean the title for filename (remove illegal chars)
    $filename = preg_replace('/[\\/:*?"<>|\r\n]+/', '', $title);
    $filename = mb_substr($filename, 0, 50); // limit length
    $filename = trim($filename);
    if ($filename === '') $filename = 'untitled';
    $filename .= '.txt';

    // 3. Remove literal 'Image' (case-insensitive, as a word)
    $text = preg_replace('/\bImage\b/i', '', $text);
    // 4. Remove standalone two-digit numbers (e.g., 01, 23)
    $text = preg_replace('/\b\d{2}\b/', '', $text);
    // 5. Remove URLs (http, https, www)
    $text = preg_replace('/https?:\/\/\S+|www\.\S+/i', '', $text);
    // 6. If contains Chinese, collapse all whitespace
    if (preg_match('/[\x{4e00}-\x{9fa5}]/u', $text)) {
        $text = preg_replace('/\s+/u', '', $text);
    } else {
        // Otherwise, collapse multiple spaces to one, trim
        $text = preg_replace('/\s+/u', ' ', trim($text));
    }
    // 7. Remove any extra blank lines
    $text = preg_replace('/\n{2,}/', "\n", $text);

    // 8. Output as file download
    header('Content-Type: text/plain; charset=UTF-8');
    header('Content-Disposition: attachment; filename="' . $filename . '"');
    header('Content-Length: ' . strlen($text));
    echo $text;
    exit;
}
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tools</title>
    <link rel="icon" type="image/png" href="Tools.png">
    <style>
        body {
            margin: 20px;
            font-family: "Microsoft YaHei", "SimSun", Arial, sans-serif;
        }
        .grid-container {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            max-width: 1200px;
            margin: 0 auto;
        }
        .form-container {
            background-color: #f5f5f5;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        textarea {
            width: 100%;
            height: 60px;
            font-family: inherit;
            padding: 8px;
            box-sizing: border-box;
            margin-bottom: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        button {
            width: 100%;
            padding: 8px;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }
        button:hover {
            background-color: #45a049;
        }
    </style>
</head>
<body>
    <div class="grid-container">
        <!-- First Form: Chinese Conversion -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="userInput" name="userInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["userInput"])) {
                        $text = $_POST["userInput"];
                        
                        function toSimplified($text) {
                            $from = 'zh-TW';
                            $to = 'zh-CN';
                            
                            $text = urlencode($text);
                            $url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl={$from}&tl={$to}&dt=t&q={$text}";
                            
                            $ch = curl_init();
                            curl_setopt($ch, CURLOPT_URL, $url);
                            curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
                            curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
                            curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0');
                            
                            $response = curl_exec($ch);
                            curl_close($ch);
                            
                            if ($response === false) {
                                return $text;
                            }
                            
                            $result = json_decode($response, true);
                            if (!$result) {
                                return $text;
                            }
                            
                            $translated_text = '';
                            foreach ($result[0] as $segment) {
                                if (isset($segment[0])) {
                                    $translated_text .= $segment[0];
                                }
                            }
                            
                            return $translated_text ?: $text;
                        }
                        
                        $converted = toSimplified($text);
                        echo htmlspecialchars($converted, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Traditional to Simplified</button>
            </form>
        </div>

        <!-- Fifth Form: Add dotted list -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="addDottedListInput" name="addDottedListInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["addDottedListInput"])) {
                        $text = $_POST["addDottedListInput"];
                        
                        // Split into lines
                        $lines = explode("\n", $text);
                        
                        // Process each line
                        $result = array_map(function($line) {
                            // Preserve original whitespace at the start
                            $originalIndent = '';
                            if (preg_match('/^(\s+)/', $line, $matches)) {
                                $originalIndent = $matches[1];
                            }
                            
                            // If line already has "- ", preserve it with its position
                            if (strpos($line, '- ') !== false) {
                                return $line;
                            }
                            
                            // Only add "- " to non-empty lines that don't already have it
                            if (trim($line) !== '') {
                                return $originalIndent . "- " . ltrim($line);
                            }
                            
                            return $line;
                        }, $lines);
                        
                        // Filter out empty lines and join back
                        $result = array_filter($result, function($line) {
                            return trim($line) !== '';
                        });
                        $result = implode("\n", $result);
                        
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Add dotted list</button>
            </form>
        </div>

        <!-- Third Form: Remove All Whitespace -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="whitespaceInput" name="whitespaceInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["whitespaceInput"])) {
                        $text = $_POST["whitespaceInput"];
                        
                        // Remove all whitespace (spaces, tabs, newlines)
                        $result = preg_replace('/\s+/', '', $text);
                        
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Remove All Whitespace</button>
            </form>
        </div>

               
        <!-- Second Form: Remove Empty Lines -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="lineInput" name="lineInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["lineInput"])) {
                        $text = $_POST["lineInput"];
                        
                        // Split text into lines, remove empty ones, and rejoin
                        $lines = explode("\n", $text);
                        $lines = array_filter($lines, function($line) {
                            return trim($line) !== '';
                        });
                        $result = implode("\n", $lines);
                        
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Remove Empty Lines</button>
            </form>
        </div>


        <!-- Fourth Form: Remove dotted list -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="dottedListInput" name="dottedListInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["dottedListInput"])) {
                        $text = $_POST["dottedListInput"];
                        
                        // Split into lines
                        $lines = explode("\n", $text);
                        
                        // Process each line
                        $result = array_map(function($line) {
                            // Remove leading tabs/spaces and "- " from each line
                            $line = preg_replace('/^[\t ]*- /', '', $line);
                            return trim($line);
                        }, $lines);
                        
                        // Filter out empty lines and join back
                        $result = array_filter($result, function($line) {
                            return !empty($line);
                        });
                        $result = implode("\n", $result);
                        
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Remove dotted list</button>
            </form>
        </div>


        <!-- Remove Markdown Form -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="markdownInput" name="markdownInput" rows="5" cols="80" placeholder="Input markdown text..."><?php 
                    if (isset($_POST["markdownInput"])) {
                        $text = $_POST["markdownInput"];
                        
                        // Remove markdown symbols
                        $patterns = array(
                            '/#{1,6}\s/' => '',           // Headers
                            '/\*\*([^*]+)\*\*/' => '$1',  // Bold
                            '/\*([^*]+)\*/' => '$1',      // Italic
                            '/^-\s/' => '',               // List items
                            '/^>\s/' => '',               // Blockquotes
                            '/`([^`]+)`/' => '$1',        // Inline code
                            '/---+/' => '',               // Horizontal rules
                            '/\[\[([^\]]+)\]\]/' => '$1', // Remove [[brackets]]
                            '/\[([^\]]+)\]/' => '$1',     // Remove [brackets]
                        );
                        
                        $text = preg_replace(array_keys($patterns), array_values($patterns), $text);
                        
                        // Normalize multiple blank lines to single blank line
                        $lines = explode("\n", $text);
                        $result = "";
                        $prevLineEmpty = false;
                        
                        foreach ($lines as $line) {
                            $currentLine = trim($line);
                            $currentLineEmpty = (strlen($currentLine) === 0);
                            
                            if (!($prevLineEmpty && $currentLineEmpty)) {
                                $result .= $currentLine . "\n";
                            }
                            
                            $prevLineEmpty = $currentLineEmpty;
                        }
                        
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Remove Markdown Formatting</button>
            </form>
        </div>


        <!-- Web Page Text Cleaner Form -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="webPageInput" name="webPageInput" rows="5" cols="80" placeholder="Paste web page text here..."><?php 
                    if (isset($_POST["webPageInput"])) {
                        $text = $_POST["webPageInput"];

                        // 1. Strictly capture the first line as title (up to first line break)
                        $lines = preg_split('/\r?\n/', $text, 2);
                        $title = isset($lines[0]) ? trim($lines[0]) : '';

                        // 2. Clean the title for filename (remove illegal chars)
                        $filename = preg_replace('/[\\/:*?"<>|\r\n]+/', '', $title);
                        $filename = mb_substr($filename, 0, 50); // limit length
                        $filename = trim($filename);
                        if ($filename === '') $filename = 'untitled';
                        $filename .= '.txt';

                        // 3. Remove literal 'Image' (case-insensitive, as a word)
                        $text = preg_replace('/\bImage\b/i', '', $text);
                        // 4. Remove standalone two-digit numbers (e.g., 01, 23)
                        $text = preg_replace('/\b\d{2}\b/', '', $text);
                        // 5. Remove URLs (http, https, www)
                        $text = preg_replace('/https?:\/\/\S+|www\.\S+/i', '', $text);
                        // 6. If contains Chinese, collapse all whitespace
                        if (preg_match('/[\x{4e00}-\x{9fa5}]/u', $text)) {
                            $text = preg_replace('/\s+/u', '', $text);
                        } else {
                            // Otherwise, collapse multiple spaces to one, trim
                            $text = preg_replace('/\s+/u', ' ', trim($text));
                        }
                        // 7. Remove any extra blank lines
                        $text = preg_replace('/\n{2,}/', "\n", $text);

                        // 8. Output as file download
                        header('Content-Type: text/plain; charset=UTF-8');
                        header('Content-Disposition: attachment; filename="' . $filename . '"');
                        header('Content-Length: ' . strlen($text));
                        echo $text;
                        exit;
                    }
                ?></textarea>
                <button type="submit">Clean Web Page Text</button>
            </form>
        </div>

        <!-- Remove All Number Markers (Arabic & Chinese) Form -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="allNumberMarkersInput" name="allNumberMarkersInput" rows="5" cols="80" placeholder="Input text..."><?php 
                    if (isset($_POST["allNumberMarkersInput"])) {
                        $text = $_POST["allNumberMarkersInput"];
                        
                        // Remove all numerical markers at the start of lines (Arabic and Chinese, including bold-wrapped)
                        $lines = explode("\n", $text);
                        $result = array_map(function($line) {
                            // 1. Remove bold-wrapped Arabic number markers: **10. ...**
                            $line = preg_replace('/^(\*{2})([ \t]*\d+(?:[\.\d]*)?)([\.、\)\）\:，]|\s)+(.*)\1/u', '**$4**', $line);
                            // 2. Remove normal Arabic number markers: 10. ...
                            $line = preg_replace('/^([ \t]*)(\d+(?:[\.\d]*)?)([\.、\)\）\:，]|\s)+/u', '$1', $line);
                            // 3. Remove Chinese enumeration marker (一、二、etc. with 、 or ，) at the start, preserving all other markdown and inline markdown
                            if (preg_match('/^(#{1,6}\s*)?([一二三四五六七八九十百千]+)[、，](.*)/u', $line, $matches)) {
                                $header = isset($matches[1]) ? $matches[1] : '';
                                $content = $matches[3];
                                return $header . $content;
                            }
                            // 4. Remove all leading spaces before a dash for bullet points (but not for headers)
                            $line = preg_replace('/^\s*-(\s+)/', '-$1', $line);
                            return $line;
                        }, $lines);
                        $result = implode("\n", $result);
                        echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                    }
                ?></textarea>
                <button type="submit">Remove All Number Markers</button>
            </form>
        </div>

        
        <!-- XML to Raw Text Form -->
        <div class="form-container">
            <form method="post" action="">
                <textarea id="xmlInput" name="xmlInput" rows="5" cols="80" placeholder="Input XML text..."><?php 
                    if (isset($_POST["xmlInput"])) {
                        $text = $_POST["xmlInput"];
                        
                        // Load XML string
                        libxml_use_internal_errors(true); // Suppress XML errors
                        $dom = new DOMDocument();
                        $dom->loadXML($text);
                        
                        // Function to extract text from XML nodes
                        function extractText(DOMNode $node) {
                            $result = '';
                            if ($node->nodeType === XML_TEXT_NODE) {
                                $text = trim($node->nodeValue);
                                if (!empty($text)) {
                                    $result .= $text . "\n";
                                }
                            }
                            if ($node->hasChildNodes()) {
                                foreach ($node->childNodes as $child) {
                                    $result .= extractText($child);
                                }
                            }
                            return $result;
                        }
                        
                        // Function to process text based on language
                        function processText($text) {
                            // Check if text contains Chinese characters
                            if (preg_match('/[\x{4e00}-\x{9fa5}]/u', $text)) {
                                // For Chinese text, remove all spaces
                                return preg_replace('/\s+/u', '', $text);
                            } else {
                                // For English text, ensure single spaces between words
                                return preg_replace('/\s+/u', ' ', trim($text));
                            }
                        }
                        
                        // Extract text and clean up
                        if ($dom->documentElement) {
                            $result = extractText($dom->documentElement);
                            // Split into lines and process each line
                            $lines = explode("\n", trim($result));
                            $processed_lines = array_map('processText', $lines);
                            // Filter empty lines and join
                            $processed_lines = array_filter($processed_lines);
                            $result = implode(' ', $processed_lines);
                            echo htmlspecialchars($result, ENT_QUOTES, 'UTF-8');
                        } else {
                            // If XML parsing failed, return original text
                            echo htmlspecialchars($text, ENT_QUOTES, 'UTF-8');
                        }
                    }
                ?></textarea>
                <button type="submit">Convert XML to Raw Text</button>
            </form>
        </div>



    </div>
</body>
</html>
