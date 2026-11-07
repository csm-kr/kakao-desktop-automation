import AppKit
import ApplicationServices
import Foundation

func a(_ e: AXUIElement,_ key:String)->CFTypeRef? { var v:CFTypeRef?;return AXUIElementCopyAttributeValue(e,key as CFString,&v) == .success ? v:nil }
func kids(_ e:AXUIElement)->[AXUIElement]{a(e,kAXChildrenAttribute) as? [AXUIElement] ?? []}
func role(_ e:AXUIElement)->String{a(e,kAXRoleAttribute) as? String ?? ""}
func find(_ e:AXUIElement,_ predicate:(AXUIElement)->Bool,_ depth:Int=0)->AXUIElement?{
 if predicate(e){return e};if depth>=10 || ["AXTable","AXOutline"].contains(role(e)){return nil}
 for c in kids(e){if let f=find(c,predicate,depth+1){return f}};return nil
}
func firstText(_ e:AXUIElement,_ depth:Int=0)->String?{
 if depth>8{return nil};if role(e)=="AXStaticText",let s=a(e,kAXValueAttribute) as? String{return s}
 for c in kids(e){if let s=firstText(c,depth+1){return s}};return nil
}
func dump(_ e:AXUIElement,_ path:String,_ depth:Int=0)->[[String:Any]]{
 if depth>8{return []};var d:[String:Any]=["path":path,"role":role(e)]
 for k in [kAXTitleAttribute,kAXDescriptionAttribute,kAXValueAttribute,kAXHelpAttribute,kAXIdentifierAttribute]{if let s=a(e,k) as? String{d[k]=String(s.prefix(8000))}}
 var actions:CFArray?;if AXUIElementCopyActionNames(e,&actions) == .success{d["actions"]=actions as? [String] ?? []}
 var result=[d];for (i,c) in kids(e).prefix(80).enumerated(){result += dump(c,path+".\(i)",depth+1)};return result
}
let mode=CommandLine.arguments.dropFirst().first ?? "search"
let supplied=Array(CommandLine.arguments.dropFirst())
if supplied == ["--help"] {
 print("Usage: kakao-room-nav search <query> | open <exact-title> <row-index> | step <exact-title> <0...1> | scroll-info <exact-title> | labels <exact-title> | page <exact-title>\nSearch/open can activate KakaoTalk. Reads and scrolls only; never sends messages. Output may contain private room text.")
 exit(0)
}
guard ["search","open","step","scroll-info","labels","page"].contains(mode) else {print("{\"error\":\"unknown_mode\"}");exit(2)}
guard supplied.count>=2,!supplied[1].isEmpty else {print("{\"error\":\"explicit_query_or_title_required\"}");exit(2)}
let requested=supplied[1]
let session=CGSessionCopyCurrentDictionary() as? [String:Any] ?? [:]
guard session["CGSSessionScreenIsLocked"] as? Bool != true,
 session["kCGSessionLoginDoneKey"] as? Bool != false,
 session["kCGSSessionOnConsoleKey"] as? Bool != false else {print("{\"error\":\"session_not_interactive\"}");exit(2)}
guard AXIsProcessTrusted(),let app=NSRunningApplication.runningApplications(withBundleIdentifier:"com.kakao.KakaoTalkMac").first else {exit(2)}
let ax=AXUIElementCreateApplication(app.processIdentifier);AXUIElementSetMessagingTimeout(ax,1)
if !["labels","page","scroll-info","step"].contains(mode) {app.activate(options:[]);RunLoop.current.run(until:Date().addingTimeInterval(0.4))}
var output:[String:Any]=["mode":mode,"pid":app.processIdentifier,"messages_sent":0]
let windows=a(ax,kAXWindowsAttribute) as? [AXUIElement] ?? []
if mode=="search" {
 guard let main=windows.first(where:{a($0,kAXIdentifierAttribute) as? String == "Main Window"}) else {print("{\"error\":\"main_missing\"}");exit(2)}
 if find(main,{role($0)=="AXTextField"})==nil,let button=find(main,{role($0)=="AXButton" && a($0,kAXDescriptionAttribute) as? String == "검색"}){
  output["search_open"]=AXUIElementPerformAction(button,kAXPressAction as CFString).rawValue;RunLoop.current.run(until:Date().addingTimeInterval(0.25))
 }
 guard let field=find(main,{role($0)=="AXTextField"}) else {print("{\"error\":\"search_field_missing\"}");exit(2)}
 let query=requested
 if a(field,kAXValueAttribute) as? String != query{
  _=AXUIElementSetAttributeValue(field,kAXValueAttribute as CFString,"" as CFString)
  _=AXUIElementSetAttributeValue(field,kAXFocusedAttribute as CFString,kCFBooleanTrue)
  output["search_insert"]=AXUIElementSetAttributeValue(field,kAXSelectedTextAttribute as CFString,query as CFString).rawValue
  RunLoop.current.run(until:Date().addingTimeInterval(0.8))
 }
 output["query_verified"]=a(field,kAXValueAttribute) as? String == query
 guard let table=find(main,{role($0)=="AXTable"}) else {print("{\"error\":\"list_missing\"}");exit(2)}
 let rows=a(table,"AXRows") as? [AXUIElement] ?? kids(table)
 var matches:[[String:Any]]=[]
 for (i,row) in rows.enumerated(){if let name=firstText(row),name.localizedCaseInsensitiveContains(query){matches.append(["title":name,"row_index":i,"nodes":dump(row,"row")])}}
 output["matches"]=matches
} else if mode=="open" {
 let args=Array(CommandLine.arguments.dropFirst());guard args.count==3,let index=Int(args[2]),!args[1].isEmpty,
 let main=windows.first(where:{a($0,kAXIdentifierAttribute) as? String == "Main Window"}),let table=find(main,{role($0)=="AXTable"}) else{exit(2)}
 let rows=a(table,"AXRows") as? [AXUIElement] ?? kids(table)
 guard rows.indices.contains(index),firstText(rows[index])==args[1],let cell=kids(rows[index]).first else{exit(2)}
 output["preopen_row"]=dump(rows[index],"row")
 _=AXUIElementSetAttributeValue(table,"AXSelectedRows" as CFString,[rows[index]] as CFArray)
 output["context"]=AXUIElementPerformAction(cell,"AXShowMenu" as CFString).rawValue
 RunLoop.current.run(until:Date().addingTimeInterval(0.25))
 guard let menu=(kids(ax)+kids(cell)).first(where:{role($0)=="AXMenu"}) else{print("{\"error\":\"context_menu_missing\"}");exit(2)}
 let open=kids(menu).filter{["채팅방 열기","Open chat","Open Chat"].contains(a($0,kAXTitleAttribute) as? String ?? "")}
 guard open.count==1 else{exit(2)}
 output["open"]=AXUIElementPerformAction(open[0],kAXPressAction as CFString).rawValue
 RunLoop.current.run(until:Date().addingTimeInterval(0.6))
 output["windows"]=(a(ax,kAXWindowsAttribute) as? [AXUIElement] ?? []).map{["title":a($0,kAXTitleAttribute) as? String ?? "", "identifier":a($0,kAXIdentifierAttribute) as? String ?? ""]}
} else if ["page","scroll-info","step"].contains(mode) {
 let target=requested
 var exact=windows.filter{a($0,kAXTitleAttribute) as? String==target}
 if exact.isEmpty,let value=a(ax,kAXFocusedWindowAttribute),CFGetTypeID(value)==AXUIElementGetTypeID(){let w=unsafeBitCast(value,to:AXUIElement.self);if a(w,kAXTitleAttribute) as? String==target{exact=[w]}}
 guard exact.count==1,let table=find(exact[0],{role($0)=="AXTable"}) else{exit(2)}
 var current:AXUIElement?=table;var scroll:AXUIElement?
 for _ in 0..<8{
  guard let e=current else{break};var actions:CFArray?;AXUIElementCopyActionNames(e,&actions)
  if (actions as? [String] ?? []).contains("AXScrollDownByPage"){scroll=e;break}
  if let p=a(e,kAXParentAttribute),CFGetTypeID(p)==AXUIElementGetTypeID(){current=unsafeBitCast(p,to:AXUIElement.self)}else{break}
 }
 guard let scroll=scroll else{print("{\"error\":\"scroll_action_missing\"}");exit(2)}
 func barValue()->Any{if let b=a(scroll,kAXVerticalScrollBarAttribute),CFGetTypeID(b)==AXUIElementGetTypeID(){return a(unsafeBitCast(b,to:AXUIElement.self),kAXValueAttribute) as? NSNumber ?? NSNull()};return NSNull()}
 output["before_scroll_value"]=barValue()
 output["scroll_role"]=role(scroll)
 if let b=a(scroll,kAXVerticalScrollBarAttribute),CFGetTypeID(b)==AXUIElementGetTypeID(){
  let bar=unsafeBitCast(b,to:AXUIElement.self);var settable:DarwinBoolean=false;output["bar_settable_error"]=AXUIElementIsAttributeSettable(bar,kAXValueAttribute as CFString,&settable).rawValue;output["bar_settable"]=settable.boolValue
  var actions:CFArray?;AXUIElementCopyActionNames(bar,&actions);output["bar_actions"]=actions as? [String] ?? []
  if mode=="step",settable.boolValue,CommandLine.arguments.count==4,let value=Double(CommandLine.arguments[3]),(0...1).contains(value){output["set_scroll_error"]=AXUIElementSetAttributeValue(bar,kAXValueAttribute as CFString,NSNumber(value:value)).rawValue}
 }
 if mode=="page"{output["scroll_error"]=AXUIElementPerformAction(scroll,"AXScrollDownByPage" as CFString).rawValue}
 RunLoop.current.run(until:Date().addingTimeInterval(0.65))
 output["after_scroll_value"]=barValue()
 output["rows_after"]=(a(table,"AXRows") as? [AXUIElement] ?? []).count
} else if mode=="labels" {
 let target=requested
 var exact=windows.filter{a($0,kAXTitleAttribute) as? String == target}
 if exact.isEmpty,let value=a(ax,kAXFocusedWindowAttribute),CFGetTypeID(value)==AXUIElementGetTypeID(){let w=unsafeBitCast(value,to:AXUIElement.self);if a(w,kAXTitleAttribute) as? String==target{exact=[w]}}
 guard exact.count==1,let table=find(exact[0],{role($0)=="AXTable"}) else {exit(2)}
 let rows=a(table,"AXRows") as? [AXUIElement] ?? kids(table)
 output["row_count"]=rows.count
 var markers:[[String:Any]]=[];var details:[[String:Any]]=[]
 for (i,row) in rows.enumerated(){
  if i < max(0,rows.count-220){continue}
  let nodes=dump(row,"row.\(i)")
  let labels=nodes.filter{($0["role"] as? String)=="AXStaticText"}
  details.append(["row_index":i,"labels":labels])
  let matching=nodes.filter{n in let s=["AXValue","AXDescription","AXHelp","AXTitle"].compactMap{n[$0] as? String}.joined(separator:" ");return s.contains("읽으") || s.contains("읽지 않은") || s.contains("읽은 메시지") || s.contains("새로운 메시지") || s.lowercased().contains("unread")}
  if !matching.isEmpty{markers.append(["row_index":i,"nodes":matching])}
 }
 output["row_labels"]=details;output["markers"]=markers
 func controls(_ e:AXUIElement,_ depth:Int=0)->[[String:Any]]{if depth>10 || ["AXTable","AXOutline"].contains(role(e)){return []};var result:[[String:Any]]=[];if ["AXStaticText","AXButton"].contains(role(e)){var d:[String:Any]=["role":role(e)];for k in [kAXDescriptionAttribute,kAXTitleAttribute,kAXValueAttribute,kAXHelpAttribute]{if let s=a(e,k) as? String{d[k]=s}};result.append(d)};for c in kids(e){result += controls(c,depth+1)};return result}
 output["controls"]=controls(exact[0])
}
let data=try JSONSerialization.data(withJSONObject:output,options:[.prettyPrinted,.sortedKeys]);print(String(data:data,encoding:.utf8)!)
